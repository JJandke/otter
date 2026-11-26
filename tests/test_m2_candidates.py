"""Synthetic candidate/normalization tests; no account, database or browser."""

import copy
import json
import unittest
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from otter.models import SnapshotCandidate
from otter.normalization import utc_microseconds
from provadis_m1.errors import (
    AuthenticationError, InvalidResponse, ServiceError, SuspiciousResponse,
)
from provadis_m1.validation import (
    MAX_RESPONSE_BYTES, parse_candidate_response, validate_response,
)

FIXTURES = Path(__file__).parent / "fixtures"


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((FIXTURES / "valid.json").read_bytes())

    def parse(self, payload=None):
        return parse_candidate_response(
            200, "application/json; charset=utf-8",
            json.dumps(self.payload if payload is None else payload).encode(),
        )

    def test_fixture_projects_only_public_fields_and_retains_offsets(self):
        candidate = self.parse()
        self.assertEqual(candidate.event_count, 2)
        self.assertEqual(candidate.normalization_version, 1)
        event = candidate.events[0]
        self.assertEqual(asdict(event), {
            "title": "Synthetic lecture A",
            "start_utc_us": utc_microseconds(datetime(2027, 3, 26, 8, tzinfo=timezone.utc)),
            "end_utc_us": utc_microseconds(datetime(2027, 3, 26, 9, 30, tzinfo=timezone.utc)),
            "start_offset_seconds": 3600, "end_offset_seconds": 3600,
            "building": "Example building", "room": "A101",
        })
        self.assertEqual(candidate.events[1].start_offset_seconds, 7200)
        self.assertIsNone(candidate.events[1].building)
        self.assertIsNone(candidate.events[1].room)

    def test_missing_null_blank_and_populated_locations(self):
        for value in (None, "", " \t ", " Bâtiment "):
            for key, attribute in (("buildingname", "building"), ("roomname", "room")):
                with self.subTest(key=key, value=value):
                    payload = copy.deepcopy(self.payload)
                    payload["data"][0][key] = value
                    expected = "Bâtiment" if value == " Bâtiment " else None
                    self.assertEqual(getattr(self.parse(payload).events[0], attribute), expected)
        for key in ("buildingname", "roomname"):
            del self.payload["data"][0][key]
        event = self.parse().events[0]
        self.assertIsNone(event.building)
        self.assertIsNone(event.room)
        # M1's existing strict contract still requires both keys.
        with self.assertRaisesRegex(InvalidResponse, "missing_event_fields"):
            validate_response(200, "application/json", json.dumps(self.payload).encode())

    def test_no_location_is_inferred_from_datasource(self):
        self.payload["data"][0].update(buildingname="", roomname="", datasource="SKED")
        event = self.parse().events[0]
        self.assertIsNone(event.building)
        self.assertIsNone(event.room)

    def test_title_and_location_normalization_preserves_internal_whitespace(self):
        self.payload["data"][0].update(
            eventtitle="  Cafe\u0301  A\nB  ", buildingname="  Ba\u0302timent  ", roomname="  A  1  ",
        )
        event = self.parse().events[0]
        self.assertEqual(event.title, "Café  A\nB")
        self.assertEqual(event.building, "Bâtiment")
        self.assertEqual(event.room, "A  1")

    def test_immutable_records_and_safe_reprs(self):
        candidate = self.parse()
        with self.assertRaises(FrozenInstanceError):
            candidate.events[0].title = "changed"
        with self.assertRaises(FrozenInstanceError):
            candidate.events = ()
        with self.assertRaises(TypeError):
            SnapshotCandidate(events=list(candidate.events))
        with self.assertRaises(TypeError):
            SnapshotCandidate(events=(object(),))
        self.assertNotIn("Synthetic lecture", repr(candidate))
        self.assertNotIn("Example building", repr(candidate.events))
        self.assertIn("event_count=2", repr(candidate))

    def test_private_and_unknown_fields_cannot_enter_candidate(self):
        event = self.payload["data"][0]
        event.update(personid=987654321, email="PRIVATE-CANARY@example.invalid",
                     instructorname="PRIVATE-CANARY", groupname="PRIVATE-CANARY",
                     datasource={"token": "PRIVATE-CANARY"}, extra="PRIVATE-CANARY")
        self.payload.update(message="PRIVATE-CANARY", extra="PRIVATE-CANARY")
        candidate = self.parse()
        self.assertNotIn("PRIVATE-CANARY", json.dumps(asdict(candidate)))
        self.assertNotIn("987654321", repr(asdict(candidate)))
        event["email"] = {"bad": "PRIVATE-CANARY"}
        with self.assertRaises(InvalidResponse) as raised:
            self.parse()
        self.assertNotIn("PRIVATE-CANARY", str(raised.exception))

    def test_all_optional_metadata_and_message_omissions_are_valid(self):
        for event in self.payload["data"]:
            for key in ("personid", "email", "instructorname", "groupname", "datasource"):
                event.pop(key)
        self.payload.pop("message")
        self.payload.pop("message2")
        self.assertEqual(self.parse().event_count, 2)

    def test_old_events_are_valid_and_not_filtered(self):
        self.payload["data"][0].update(startdate="2023-03-03T08:00:00+01:00",
                                       enddate="2023-03-03T13:00:00+01:00")
        self.assertEqual(self.parse().event_count, 2)
        self.assertEqual(self.parse().events[0].start_utc_us,
                         utc_microseconds(datetime(2023, 3, 3, 7, tzinfo=timezone.utc)))

    def test_empty_is_only_a_candidate_and_m1_remains_suspicious(self):
        body = (FIXTURES / "empty.json").read_bytes()
        candidate = parse_candidate_response(200, "application/json", body)
        self.assertEqual(candidate.events, ())
        self.assertEqual(candidate.event_count, 0)
        self.assertFalse(hasattr(candidate, "accepted"))
        with self.assertRaises(SuspiciousResponse):
            validate_response(200, "application/json", body)

    def test_legacy_summary_stays_count_only_without_normalized_event_fields(self):
        body = json.dumps(self.payload).encode()
        summary = validate_response(200, "application/json", body)
        self.assertEqual(asdict(summary), {"event_count": 2})
        self.assertEqual(repr(summary), "ValidatedSnapshot(event_count=2)")

    def test_duplicates_same_titles_and_source_order_are_preserved(self):
        self.payload["data"][1]["eventtitle"] = self.payload["data"][0]["eventtitle"]
        self.payload["data"].append(copy.deepcopy(self.payload["data"][0]))
        self.payload["count"] = 3
        candidate = self.parse()
        self.assertEqual(candidate.event_count, 3)
        self.assertEqual(candidate.events[0], candidate.events[2])
        self.assertNotEqual(candidate.events[0], candidate.events[1])

    def test_mandatory_fields_and_late_row_failure_reject_whole_response(self):
        for key in ("eventtitle", "startdate", "enddate"):
            payload = copy.deepcopy(self.payload)
            del payload["data"][1][key]
            with self.subTest(key=key), self.assertRaises(InvalidResponse):
                self.parse(payload)
        for key, value in (("eventtitle", None), ("eventtitle", "  "),
                           ("buildingname", 1), ("roomname", []),
                           ("startdate", "2027-03-26T09:00:00"),
                           ("enddate", "2027-02-30T09:00:00+01:00"),
                           ("enddate", "2020-01-01T00:00:00Z")):
            payload = copy.deepcopy(self.payload)
            payload["data"][1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidResponse):
                self.parse(payload)
        for key in ("eventtitle", "buildingname", "roomname"):
            payload = copy.deepcopy(self.payload)
            payload["data"][1][key] = "\ud800"
            with self.subTest(key=key), self.assertRaises(InvalidResponse):
                self.parse(payload)

    def test_shared_envelope_and_http_failures(self):
        for status, media, body, error in (
            (401, "application/json", b"{}", AuthenticationError),
            (403, "application/json", b"{}", AuthenticationError),
            (302, "text/html", b"", AuthenticationError),
            (503, "application/json", b"{}", ServiceError),
            (200, "text/html", b"{}", InvalidResponse),
            (200, "application/json", b"{bad", InvalidResponse),
            (200, "application/json", b'[]', InvalidResponse),
            (200, "application/json", b'{"ok":true,"ok":true}', InvalidResponse),
            (200, "application/json", b'{"ok":NaN}', InvalidResponse),
            (200, "application/json", b"\xff", InvalidResponse),
            (200, "application/json", b" " * (MAX_RESPONSE_BYTES + 1), InvalidResponse),
        ):
            with self.subTest(status=status, media=media, size=len(body)):
                with self.assertRaises(error):
                    parse_candidate_response(status, media, body)
        for key in ("ok", "data", "count"):
            payload = copy.deepcopy(self.payload)
            del payload[key]
            with self.subTest(key=key), self.assertRaises(InvalidResponse):
                self.parse(payload)
        for key, value in (("ok", False), ("ok", 1), ("data", None),
                           ("count", True), ("count", 3), ("count", -1),
                           ("message", {})):
            payload = copy.deepcopy(self.payload)
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidResponse):
                self.parse(payload)

    def test_dst_fold_and_spring_change_compare_real_instants(self):
        for start, end, minutes in (
            ("2027-10-31T02:30:00+02:00", "2027-10-31T02:15:00+01:00", 45),
            ("2027-03-28T01:30:00+01:00", "2027-03-28T03:30:00+02:00", 60),
        ):
            self.payload["data"][0].update(startdate=start, enddate=end)
            event = self.parse().events[0]
            self.assertEqual(event.end_utc_us - event.start_utc_us, minutes * 60_000_000)
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            for source, instant, offset in (
                (start, event.start_utc_us, event.start_offset_seconds),
                (end, event.end_utc_us, event.end_offset_seconds),
            ):
                restored = (epoch + timedelta(microseconds=instant)).astimezone(
                    timezone(timedelta(seconds=offset)))
                self.assertEqual(restored.isoformat(), source)

    def test_fractional_seconds_negative_offsets_and_pre_epoch_are_exact(self):
        self.payload["data"][0].update(startdate="1969-12-31T23:59:59.999999Z",
                                       enddate="1970-01-01T00:00:00.000001+00:00")
        event = self.parse().events[0]
        self.assertEqual((event.start_utc_us, event.end_utc_us), (-1, 1))
        self.payload["data"][0].update(startdate="2027-01-01T23:30:00.123456-05:30",
                                       enddate="2027-01-02T01:00:00.123456-05:30")
        event = self.parse().events[0]
        self.assertEqual(event.start_offset_seconds, -19800)
        self.assertEqual(event.start_utc_us % 1_000_000, 123456)
        with self.assertRaises(InvalidResponse):
            utc_microseconds(datetime(2027, 1, 1))

    def test_unrepresentable_utc_and_fractional_offset_fail_without_truncation(self):
        for value in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            self.payload["data"][0]["startdate"] = value
            with self.subTest(value=value), self.assertRaises(InvalidResponse):
                self.parse()
        self.payload["data"][0].update(startdate="2027-03-26T09:00:00+01:00:00.5",
                                       enddate="2027-03-26T10:30:00+01:00:00.5")
        body = json.dumps(self.payload).encode()
        self.assertEqual(validate_response(200, "application/json", body).event_count, 2)
        with self.assertRaisesRegex(InvalidResponse, "event_offset_requires_whole_seconds"):
            self.parse()
