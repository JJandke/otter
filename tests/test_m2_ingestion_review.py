"""Freshness-bound review of specific held observations, without extra fetches."""

import unittest
from datetime import timedelta
from unittest.mock import Mock

from otter._store_files import StorageError
from otter.ingestion import IngestionError

import test_m2_ingestion as fixtures
import test_m2_assessment as samples
from otter.models import SnapshotCandidate


class ObservationReviewTests(unittest.TestCase):
    setUp = fixtures.IngestionTests.setUp
    ingest = fixtures.IngestionTests.ingest

    def hold_empty(self):
        accepted = self.ingest()
        held = self.ingest(samples.snapshot())
        return accepted, held

    def test_review_accepts_same_observation_and_keeps_original_reasons(self):
        accepted, held = self.hold_empty()
        self.now += timedelta(minutes=5)
        reviewed = self.service.approve(held.observation_uuid,
                                        expected_baseline_attempt_no=accepted.attempt_no,
                                        reason="confirmed_empty")
        self.assertEqual(reviewed.observation_uuid, held.observation_uuid)
        self.assertEqual(reviewed.attempt_no, held.attempt_no)
        self.assertEqual(reviewed.status, "accepted")
        self.assertTrue(reviewed.reviewed)
        self.assertTrue(reviewed.eligible_for_sync)
        self.assertEqual(reviewed.reasons, held.reasons)
        self.assertIsNone(reviewed.reason)
        self.assertEqual(self.store.status()["attempt_count"], 2)
        self.assertEqual(self.store.last_accepted()["attempt_no"], held.attempt_no)
        self.assertEqual(self.store.attempt(held.observation_uuid)["review_reason_code"], "confirmed_empty")

    def test_first_empty_can_be_deliberately_accepted(self):
        held = self.ingest(samples.snapshot())
        result = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None,
                                      reason="confirmed_empty")
        self.assertEqual(result.status, "accepted")
        self.assertEqual(self.store.status()["attempt_count"], 1)

    def test_review_replay_never_moves_pointer_back_or_counts_twice(self):
        accepted, held = self.hold_empty()
        first = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                     reason="confirmed_empty")
        later = self.ingest(samples.snapshot(future=10))
        before = self.store.status()
        again = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                     reason="confirmed_empty")
        self.assertEqual(first, again)
        self.assertEqual(self.store.status(), before)
        self.assertEqual(self.store.last_accepted()["attempt_no"], later.attempt_no)
        with self.assertRaises(IngestionError):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                 reason="confirmed_change")

    def test_expired_candidate_is_not_approved(self):
        accepted, held = self.hold_empty()
        self.now += timedelta(hours=2, microseconds=1)
        with self.assertRaisesRegex(IngestionError, "review_requires_fresh_observation"):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                 reason="confirmed_empty")
        self.assertEqual(self.service.result(held.observation_uuid).status, "suspicious")

    def test_exact_two_hour_boundary_is_allowed(self):
        accepted, held = self.hold_empty()
        self.now += timedelta(hours=2)
        self.assertEqual(self.service.approve(held.observation_uuid,
                                              expected_baseline_attempt_no=accepted.attempt_no,
                                              reason="confirmed_empty").status, "accepted")

    def test_newer_failed_fetch_blocks_review_of_old_candidate(self):
        accepted, held = self.hold_empty()
        self.service.fetch(Mock(side_effect=RuntimeError("PRIVATE-CANARY")))
        with self.assertRaisesRegex(IngestionError, "observation_is_not_latest"):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                 reason="confirmed_empty")

    def test_wrong_baseline_is_rejected_without_changes(self):
        accepted, held = self.hold_empty()
        before = self.store.status()
        with self.assertRaisesRegex(IngestionError, "review_baseline_changed"):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None,
                                 reason="confirmed_empty")
        self.assertEqual(self.store.status(), before)

    def test_invalid_failed_and_automatically_accepted_rows_are_not_reviewable(self):
        good = self.ingest()
        bad = self.service.fetch(Mock(side_effect=RuntimeError("PRIVATE-CANARY")))
        for result, baseline in ((good, None), (bad, good.attempt_no)):
            with self.subTest(status=result.status), self.assertRaises(IngestionError):
                self.service.approve(result.observation_uuid, expected_baseline_attempt_no=baseline,
                                     reason="confirmed_empty")

    def test_review_reason_must_match_the_anomaly_and_cannot_contain_notes(self):
        accepted, held = self.hold_empty()
        for reason in ("confirmed_duplicates", "PRIVATE-CANARY"):
            with self.subTest(reason=reason), self.assertRaises(IngestionError) as raised:
                self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                     reason=reason)
            self.assertNotIn("PRIVATE-CANARY", str(raised.exception))

    def test_duplicates_can_be_confirmed_without_merging_rows(self):
        candidate = SnapshotCandidate(events=(samples.event(0), samples.event(0)))
        held = self.ingest(candidate)
        result = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None,
                                      reason="confirmed_duplicates")
        self.assertEqual(result.status, "accepted")
        self.assertEqual(self.store.load_content(result.content_id).event_count, 2)

    def test_future_decrease_can_be_confirmed(self):
        accepted = self.ingest()
        held = self.ingest(samples.snapshot(future=5))
        result = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                      reason="confirmed_change")
        self.assertEqual(result.status, "accepted")

    def test_import_review_does_not_create_live_sync_evidence(self):
        held = self.ingest(samples.snapshot(), origin="import")
        result = self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None,
                                      reason="confirmed_empty")
        self.assertFalse(result.eligible_for_sync)
        self.assertIsNone(self.store.last_successful_fetch())

    def test_clock_regressed_candidate_requires_a_new_observation(self):
        self.now = fixtures.NOW + timedelta(minutes=1)
        accepted = self.ingest(received_at=self.now)
        held = self.ingest(samples.snapshot(), received_at=fixtures.NOW)
        self.now += timedelta(minutes=5)
        with self.assertRaisesRegex(IngestionError, "review_requires_new_observation"):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                 reason="confirmed_empty")

    def test_changed_stored_content_cannot_be_approved(self):
        accepted = self.ingest()
        held = self.ingest(samples.snapshot(future=5))
        with self.store.transaction() as db:
            db.execute("UPDATE snapshot_event SET title='Changed' WHERE content_id=? AND ordinal=0", (held.content_id,))
        with self.assertRaises(StorageError):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=accepted.attempt_no,
                                 reason="confirmed_change")
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
