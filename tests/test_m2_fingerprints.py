"""Deterministic normalized content identity; no permanent event identities."""

import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from pathlib import Path

from otter.assessment import assess_snapshot
from otter.models import NormalizedEvent, SnapshotCandidate
from otter.normalization import canonical_snapshot_bytes, fingerprint_snapshot
from provadis_m1.validation import parse_candidate_response

import test_m2_assessment as fixtures


class FingerprintTests(unittest.TestCase):
    def test_format_is_explicit_and_locked_by_exact_encoding(self):
        candidate = SnapshotCandidate(events=(NormalizedEvent("A", 0, 1, 0, 0, None, None),))
        expected = (b'{"events":[["A",0,1,0,0,null,null]],'
                    b'"format_version":1,"normalization_version":1}')
        self.assertEqual(canonical_snapshot_bytes(candidate), expected)
        result = fingerprint_snapshot(candidate)
        self.assertEqual(result.sha256, hashlib.sha256(expected).hexdigest())
        self.assertEqual((result.format_version, result.normalization_version, result.event_count),
                         (1, 1, 1))
        with self.assertRaises(FrozenInstanceError):
            result.sha256 = "changed"

    def test_reordering_with_null_and_populated_locations_is_identical(self):
        row = fixtures.event(0)
        rows = (row, replace(row, building="B", room="R"), replace(row, room="Other"))
        first = SnapshotCandidate(events=rows)
        second = SnapshotCandidate(events=rows[::-1])
        self.assertEqual(canonical_snapshot_bytes(first), canonical_snapshot_bytes(second))
        self.assertEqual(fingerprint_snapshot(first), fingerprint_snapshot(second))
        self.assertEqual(first.events, rows)  # Canonicalization never mutates the candidate.

    def test_duplicate_multiplicity_changes_digest_and_is_not_deduplicated(self):
        row = fixtures.event(0)
        single = SnapshotCandidate(events=(row,))
        double = SnapshotCandidate(events=(row, row))
        self.assertNotEqual(fingerprint_snapshot(single).sha256, fingerprint_snapshot(double).sha256)
        self.assertEqual(len(json.loads(canonical_snapshot_bytes(double))["events"]), 2)

    def test_all_retained_fields_participate_in_content_identity(self):
        original = fixtures.event(0)
        before = fingerprint_snapshot(SnapshotCandidate(events=(original,))).sha256
        for key, value in (("title", "Different"), ("start_utc_us", original.start_utc_us + 1),
                           ("end_utc_us", original.end_utc_us + 1),
                           ("start_offset_seconds", 3600), ("end_offset_seconds", 3600),
                           ("building", "B"), ("room", "R")):
            candidate = SnapshotCandidate(events=(replace(original, **{key: value}),))
            with self.subTest(key=key):
                self.assertNotEqual(before, fingerprint_snapshot(candidate).sha256)

    def test_unknown_and_private_metadata_have_no_effect_on_digest(self):
        payload = json.loads((Path(__file__).parent / "fixtures" / "valid.json").read_bytes())
        first = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        payload.update(message="PRIVATE-CANARY", extra="PRIVATE-CANARY")
        for row in payload["data"]:
            row.update(personid=99999, email="PRIVATE-CANARY@example.invalid",
                       instructorname="PRIVATE-CANARY", groupname="PRIVATE-CANARY",
                       datasource="PRIVATE-CANARY", extra="PRIVATE-CANARY")
        second = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        self.assertEqual(fingerprint_snapshot(first), fingerprint_snapshot(second))
        self.assertNotIn(b"PRIVATE-CANARY", canonical_snapshot_bytes(second))

    def test_equivalent_unicode_and_empty_locations_share_identity(self):
        payload = {"ok": True, "count": 1, "data": [{
            "eventtitle": " Cafe\u0301 ", "startdate": "2027-01-01T08:00:00+01:00",
            "enddate": "2027-01-01T09:00:00+01:00", "roomname": "  ",
        }]}
        first = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        payload["data"][0].update(eventtitle="Café", roomname=None, buildingname=None)
        second = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        self.assertEqual(fingerprint_snapshot(first), fingerprint_snapshot(second))
        self.assertIn("Café".encode(), canonical_snapshot_bytes(second))

    def test_observation_time_and_policy_are_not_content_identity(self):
        candidate = fixtures.snapshot(future=3)
        now = assess_snapshot(candidate, received_at=fixtures.REFERENCE)
        later = assess_snapshot(candidate, received_at=fixtures.REFERENCE + timedelta(days=30))
        self.assertNotEqual(now.candidate_future_count, later.candidate_future_count)
        self.assertEqual(now.fingerprint, later.fingerprint)

    def test_empty_content_has_stable_identity_but_no_automatic_acceptance(self):
        candidate = SnapshotCandidate(events=())
        self.assertEqual(fingerprint_snapshot(candidate), fingerprint_snapshot(candidate))
        self.assertEqual(json.loads(canonical_snapshot_bytes(candidate))["events"], [])
        self.assertEqual(assess_snapshot(candidate, received_at=fixtures.REFERENCE).status,
                         "suspicious")
        with self.assertRaises(TypeError):
            canonical_snapshot_bytes({})
