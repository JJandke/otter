import copy
import json
import unittest
from pathlib import Path

from provadis_m1.errors import (
    AuthenticationError,
    InvalidResponse,
    ServiceError,
    SuspiciousResponse,
)
from provadis_m1.validation import MAX_RESPONSE_BYTES, validate_response

FIXTURES = Path(__file__).parent / "fixtures"


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((FIXTURES / "valid.json").read_text())

    def validate(self, payload):
        return validate_response(200, "application/json; charset=utf-8", json.dumps(payload).encode())

    def test_synthetic_valid_snapshot_and_dst_offsets(self):
        self.assertEqual(self.validate(self.payload).event_count, 2)

    def test_failures_never_produce_snapshot(self):
        cases = [
            (401, "application/json", b"{}", AuthenticationError),
            (403, "application/json", b"{}", AuthenticationError),
            (302, "text/html", b"login", AuthenticationError),
            (429, "application/json", b"{}", ServiceError),
            (503, "application/json", b"{}", ServiceError),
            (404, "application/json", b"{}", InvalidResponse),
            (204, "application/json", b"", InvalidResponse),
            (200, "text/html", b"<html>login</html>", InvalidResponse),
            (200, "application/json", b"not-json", InvalidResponse),
            (200, "application/json", b"[]", InvalidResponse),
            (200, "application/json", b"{}", InvalidResponse),
            (200, "application/json", b"{\"ok\":true,\"ok\":false}", InvalidResponse),
            (200, "application/json", b"{\"ok\":NaN}", InvalidResponse),
            (200, "application/json", b"\xff", InvalidResponse),
            (200, "application/json", b" " * (MAX_RESPONSE_BYTES + 1), InvalidResponse),
        ]
        for status, content_type, body, error in cases:
            with self.subTest(status=status, content_type=content_type, size=len(body)):
                with self.assertRaises(error):
                    validate_response(status, content_type, body)

    def test_empty_is_inconclusive_and_not_success(self):
        with self.assertRaises(SuspiciousResponse):
            validate_response(200, "application/json", (FIXTURES / "empty.json").read_bytes())

    def test_api_reported_failure_is_not_success(self):
        with self.assertRaises(InvalidResponse):
            validate_response(200, "application/json", (FIXTURES / "failure.json").read_bytes())

    def test_required_fields_and_strict_envelope_types(self):
        for key in self.payload:
            modified = copy.deepcopy(self.payload)
            del modified[key]
            with self.subTest(missing=key), self.assertRaises(InvalidResponse):
                self.validate(modified)
        for key, value in [
            ("ok", 1), ("ok", "true"), ("message", {}), ("message2", []),
            ("data", {}), ("data", None), ("count", True), ("count", "2"),
            ("count", -1), ("count", 3),
        ]:
            modified = copy.deepcopy(self.payload)
            modified[key] = value
            with self.subTest(field=key, value=value), self.assertRaises(InvalidResponse):
                self.validate(modified)

    def test_required_event_fields(self):
        for key in ("eventtitle", "startdate", "enddate", "buildingname", "roomname"):
            modified = copy.deepcopy(self.payload)
            del modified["data"][0][key]
            with self.subTest(missing=key), self.assertRaises(InvalidResponse):
                self.validate(modified)

    def test_invalid_event_fields(self):
        for key, value in [
            ("eventtitle", " "), ("eventtitle", None), ("roomname", 42),
            ("buildingname", []), ("email", {}), ("instructorname", 123),
            ("startdate", "2027-03-26T09:00:00"),
            ("startdate", "nonsense"), ("startdate", None),
            ("enddate", "2027-03-26T08:00:00+01:00"),
            ("enddate", "2027-03-26T09:00:00+01:00"),
        ]:
            modified = copy.deepcopy(self.payload)
            modified["data"][0][key] = value
            with self.subTest(field=key, value=value), self.assertRaises(InvalidResponse):
                self.validate(modified)

    def test_offsets_compared_as_instants(self):
        self.payload["data"][0].update({
            "startdate": "2027-10-31T02:30:00+02:00",
            "enddate": "2027-10-31T02:15:00+01:00",
        })
        self.assertEqual(self.validate(self.payload).event_count, 2)

    def test_unknown_fields_do_not_break_compatible_responses(self):
        self.payload["new_field"] = "example"
        self.payload["data"][0]["new_field"] = "example"
        self.assertEqual(self.validate(self.payload).event_count, 2)

    def test_invalid_event_object(self):
        self.payload["data"][0] = None
        with self.assertRaises(InvalidResponse):
            self.validate(self.payload)
