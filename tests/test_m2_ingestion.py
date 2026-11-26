"""File-backed observation lifecycle with injected providers and clocks."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from otter._store_files import DATABASE, StorageError
from otter.assessment import AssessmentPolicy
from otter.ingestion import CapturedSnapshot, IngestionError, IngestionService, ObservationContext
from otter.models import SnapshotCandidate
from otter.normalization import fingerprint_snapshot
from otter.storage import Store, initialize_store
from provadis_m1.errors import (
    AuthenticationError, BootstrapError, ConfigurationError, InvalidResponse,
    NetworkError, ServiceError, SuspiciousResponse,
)
from provadis_m1.validation import parse_candidate_response

import test_m2_assessment as samples

NOW = samples.REFERENCE


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / ".otter-data"
        initialize_store(self.directory)
        self.store = Store(self.directory)
        self.addCleanup(self.store.close)
        self.now = NOW
        self.service = IngestionService(self.store, clock=lambda: self.now)

    def ingest(self, candidate=None, *, origin="live", context=None, received_at=NOW):
        context = context or ObservationContext(str(uuid4()), origin, NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10) if candidate is None else candidate, received_at)
        return self.service.ingest_captured(context, capture)

    def test_success_persists_complete_observation_and_pointer(self):
        result = self.ingest()
        self.assertEqual(result.status, "accepted")
        self.assertTrue(result.eligible_for_sync)
        self.assertEqual(self.store.last_accepted()["attempt_no"], result.attempt_no)
        self.assertEqual(self.store.load_content(result.content_id).event_count, 10)
        row = self.store.attempt(result.observation_uuid)
        self.assertEqual((row["http_status"], row["reported_count"], row["policy_version"]), (200, 10, 1))
        self.assertIsNotNone(row["capture_digest"])

    def test_separate_equal_observations_share_content_not_attempt(self):
        results = [self.ingest() for _ in range(3)]
        self.assertEqual(len({r.observation_uuid for r in results}), 3)
        self.assertEqual(len({r.content_id for r in results}), 1)
        self.assertEqual((self.store.status()["attempt_count"], self.store.status()["content_count"]), (3, 1))

    def test_replay_is_idempotent_even_after_later_observation(self):
        context = ObservationContext(str(uuid4()), "live", NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), NOW)
        first = self.service.ingest_captured(context, capture)
        later = self.ingest(samples.snapshot(future=11))
        before = self.store.status()
        self.assertEqual(self.service.ingest_captured(context, capture), first)
        reordered = CapturedSnapshot(SnapshotCandidate(events=capture.candidate.events[::-1]), NOW)
        self.assertEqual(self.service.ingest_captured(context, reordered), first)
        self.assertEqual(self.store.status(), before)
        self.assertEqual(self.store.last_accepted()["attempt_no"], later.attempt_no)

    def test_conflicting_uuid_context_time_or_content_cannot_overwrite(self):
        context = ObservationContext(str(uuid4()), "live", NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), NOW)
        first = self.service.ingest_captured(context, capture)
        pairs = ((replace(context, origin="import"), capture),
                 (replace(context, started_at=NOW + timedelta(microseconds=1)), capture),
                 (context, replace(capture, received_at=NOW + timedelta(microseconds=1))),
                 (context, CapturedSnapshot(samples.snapshot(future=11), NOW)))
        for changed_context, changed_capture in pairs:
            with self.subTest(context=changed_context), self.assertRaisesRegex(IngestionError, "observation_id_conflict"):
                self.service.ingest_captured(changed_context, changed_capture)
        self.assertEqual(self.service.result(context.observation_uuid), first)
        self.assertEqual(self.store.status()["attempt_count"], 1)

    def test_same_instant_in_another_timezone_is_same_context(self):
        context = ObservationContext(str(uuid4()), "live", NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), NOW)
        first = self.service.ingest_captured(context, capture)
        tz = timezone(timedelta(hours=2))
        self.assertEqual(self.service.ingest_captured(replace(context, started_at=NOW.astimezone(tz)),
                                                     replace(capture, received_at=NOW.astimezone(tz))), first)

    def test_policy_change_does_not_reassess_an_already_recorded_receipt(self):
        context = ObservationContext(str(uuid4()), "live", NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), NOW)
        first = self.service.ingest_captured(context, capture)
        other = IngestionService(self.store, clock=lambda: self.now, policy=AssessmentPolicy(1, 0))
        self.assertEqual(other.ingest_captured(context, capture), first)

    def test_empty_and_large_future_drop_are_held_without_pointer_change(self):
        accepted = self.ingest()
        for candidate in (samples.snapshot(), samples.snapshot(future=5)):
            result = self.ingest(candidate)
            self.assertEqual(result.status, "suspicious")
            self.assertFalse(result.eligible_for_sync)
            self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
            self.assertEqual(self.store.last_successful_fetch()["attempt_no"], result.attempt_no)

    def test_failed_or_suspicious_observations_never_become_the_baseline(self):
        self.ingest()
        first_held = self.ingest(samples.snapshot(future=5))
        self.service.fetch(Mock(side_effect=NetworkError()))
        second_held = self.ingest(samples.snapshot(future=5))
        self.assertEqual((first_held.status, second_held.status), ("suspicious", "suspicious"))
        self.assertEqual(self.store.attempt(first_held.observation_uuid)["baseline_attempt_no"],
                         self.store.attempt(second_held.observation_uuid)["baseline_attempt_no"])

    def test_semester_progression_then_new_semester_growth_is_accepted(self):
        self.ingest(samples.snapshot(future=10, past=60))
        progress = self.ingest(samples.snapshot(future=10))
        semester = self.ingest(samples.snapshot(future=260))
        self.assertEqual((progress.status, semester.status), ("accepted", "accepted"))
        self.assertIn("future_event_count_increased", semester.information)

    def test_old_only_and_unknown_locations_remain_valid(self):
        result = self.ingest(samples.snapshot(past=1))
        self.assertEqual(result.status, "accepted")
        event = self.store.load_content(result.content_id).events[0]
        self.assertIsNone(event.building)
        self.assertIsNone(event.room)

    def test_duplicates_are_preserved_in_quarantine(self):
        candidate = SnapshotCandidate(events=(samples.event(0), samples.event(0)))
        result = self.ingest(candidate)
        self.assertEqual(result.reasons, ("duplicate_events",))
        self.assertEqual(self.store.load_content(result.content_id).event_count, 2)

    def test_imports_are_distinct_and_ineligible_for_future_sync(self):
        result = self.ingest(origin="import")
        self.assertEqual(result.status, "accepted")
        self.assertFalse(result.eligible_for_sync)
        self.assertIsNone(self.store.last_successful_fetch())
        self.assertIsNone(self.store.attempt(result.observation_uuid)["http_status"])

    def test_provider_runs_once_with_committed_start_and_no_sql_transaction(self):
        def provider():
            self.assertEqual(self.store.status()["attempt_count"], 1)
            self.assertFalse(self.store._db().in_transaction)
            raw = sqlite3.connect(self.directory / DATABASE, autocommit=True, timeout=0.1)
            try:
                raw.execute("BEGIN IMMEDIATE")
                raw.execute("ROLLBACK")
            finally:
                raw.close()
            return CapturedSnapshot(samples.snapshot(future=10), NOW)
        fetch = Mock(side_effect=provider)
        result = self.service.fetch(fetch)
        fetch.assert_called_once()
        self.assertEqual(result.status, "accepted")

    def test_provider_failures_preserve_accepted_data_and_discard_error_details(self):
        accepted = self.ingest()
        cases = ((InvalidResponse("malformed_json"), "invalid"),
                 (AuthenticationError("api_access_denied"), "failed"),
                 (NetworkError("direct_api_timeout"), "failed"),
                 (ServiceError("api_temporarily_unavailable"), "failed"),
                 (BootstrapError(), "failed"), (ConfigurationError("PRIVATE-CANARY"), "failed"),
                 (SuspiciousResponse(), "failed"), (RuntimeError("PRIVATE-CANARY"), "failed"))
        for error, status in cases:
            provider = Mock(side_effect=error)
            result = self.service.fetch(provider)
            with self.subTest(kind=type(error).__name__):
                self.assertEqual(result.status, status)
                self.assertFalse(result.eligible_for_sync)
                self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
                self.assertNotIn("PRIVATE-CANARY", json.dumps(result.safe_summary()))
            provider.assert_called_once()
        self.assertNotIn(b"PRIVATE-CANARY", (self.directory / DATABASE).read_bytes())

    def test_real_parser_failure_is_recorded_without_partial_snapshot(self):
        accepted = self.ingest()
        def provider():
            parse_candidate_response(200, "application/json", b'{"ok":true,"count":1,"data":[]}')
        result = self.service.fetch(provider)
        self.assertEqual((result.status, result.reason), ("invalid", "count_mismatch"))
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
        self.assertEqual(self.store.status()["content_count"], 1)

    def test_wrong_provider_contract_fails_closed(self):
        result = self.service.fetch(lambda: {"PRIVATE-CANARY": "not a capture"})
        self.assertEqual((result.status, result.reason), ("failed", "provider_contract_failed"))
        self.assertNotIn(b"PRIVATE-CANARY", (self.directory / DATABASE).read_bytes())

    def test_keyboard_interrupt_is_recorded_and_reraised(self):
        provider = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.service.fetch(provider)
        with self.store._read() as db:
            row = db.execute("SELECT * FROM fetch_attempt").fetchone()
        self.assertEqual((row["status"], row["reason_code"]), ("interrupted", "operator_interrupted"))
        self.assertIsNone(self.store.last_accepted())

    def test_provider_exit_text_cannot_escape_through_an_interruption(self):
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.service.fetch(Mock(side_effect=SystemExit("PRIVATE-CANARY")))
        self.assertEqual(raised.exception.args, ())
        self.assertNotIn(b"PRIVATE-CANARY", (self.directory / DATABASE).read_bytes())

    def test_result_and_capture_repr_are_private_and_immutable(self):
        context = ObservationContext(str(uuid4()), "live", NOW)
        candidate = SnapshotCandidate(events=(replace(samples.event(0), title="TIMETABLE-CANARY"),))
        capture = CapturedSnapshot(candidate, NOW)
        result = self.service.ingest_captured(context, capture)
        with self.assertRaises(FrozenInstanceError):
            result.status = "failed"
        output = repr(capture) + repr(result) + json.dumps(result.safe_summary()) + json.dumps(self.store.status())
        self.assertNotIn("TIMETABLE-CANARY", output)
        self.assertNotIn(fingerprint_snapshot(candidate).sha256, output)

    def test_input_validation_precedes_database_writes(self):
        for args in (("bad", "live", NOW), (str(uuid4()), "bad", NOW),
                     (str(uuid4()), "live", NOW.replace(tzinfo=None))):
            with self.subTest(args=args), self.assertRaises(IngestionError):
                ObservationContext(*args)
        with self.assertRaises(IngestionError):
            CapturedSnapshot(None, NOW)
        with self.assertRaises(IngestionError):
            CapturedSnapshot(samples.snapshot(future=1), NOW.replace(tzinfo=None))
        self.assertEqual(self.store.status()["attempt_count"], 0)

    def test_future_clock_regression_is_quarantined(self):
        self.now = NOW + timedelta(minutes=1)
        self.ingest(received_at=self.now)
        result = self.ingest(received_at=NOW)
        self.assertEqual(result.status, "suspicious")
        self.assertIn("observation_time_regressed", result.reasons)
