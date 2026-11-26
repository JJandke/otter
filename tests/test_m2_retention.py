"""Quarantine expiry preserves accepted history and observation identity."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from otter.__main__ import main
from otter._store_files import StorageError
from otter.backups import backup_store, check_store, restore_backup, verify_backup
from otter.ingestion import CapturedSnapshot, IngestionService, ObservationContext
from otter.models import SnapshotCandidate
from otter.normalization import canonical_snapshot_bytes, fingerprint_snapshot
from otter.retention import prune_quarantine
from otter.storage import Store, initialize_store

import test_m2_storage as fixtures


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "data"
        initialize_store(self.directory)
        self.now = fixtures.NOW
        self.store = Store(self.directory)
        self.addCleanup(self.store.close)
        self.service = IngestionService(self.store, clock=lambda: self.now)

    def ingest(self, candidate=None):
        context = ObservationContext(str(uuid4()), "import", self.now)
        capture = CapturedSnapshot(SnapshotCandidate(events=fixtures.candidate().events * 2)
                                   if candidate is None else candidate, self.now)
        return context, capture, self.service.ingest_captured(context, capture)

    def expire(self, *, apply=True, days=31):
        return prune_quarantine(self.directory, now=fixtures.NOW + timedelta(days=days), apply=apply)

    def test_default_preview_does_not_change_payload_metadata_or_create_backup(self):
        _, _, held = self.ingest()
        before = self.store.status()
        summary = self.expire(apply=False)
        self.assertTrue(summary["dry_run"])
        self.assertEqual((summary["eligible_content_count"], summary["eligible_event_rows"]), (1, 4))
        self.assertEqual(self.store.status(), before)
        self.assertFalse((self.directory / "backups").exists())
        self.assertEqual(self.store.load_content(held.content_id).event_count, 4)

    def test_apply_expires_only_payload_and_keeps_receipt_and_idempotent_result(self):
        context, capture, held = self.ingest()
        row = self.store.attempt(held.observation_uuid)
        summary = self.expire()
        self.assertEqual(summary["expired_content_count"], 1)
        self.assertEqual(self.store.attempt(held.observation_uuid), row)
        self.assertEqual(self.store.status()["attempt_count"], 1)
        self.assertTrue(self.store.content_expired(held.content_id))
        with self.assertRaisesRegex(StorageError, "content_expired"):
            self.store.load_content(held.content_id)
        self.assertEqual(self.service.ingest_captured(context, capture), held)
        self.assertTrue(self.store.content_expired(held.content_id))  # Replay is read-only.
        self.assertEqual(check_store(self.directory)["expired_content_count"], 1)
        self.assertEqual(verify_backup(self.directory / "backups" / summary["backup_name"])["expired_content_count"], 0)

    def test_old_accepted_contents_and_baseline_can_never_be_pruned(self):
        _, _, first = self.ingest(fixtures.candidate())
        self.now += timedelta(days=1)
        _, _, latest = self.ingest(SnapshotCandidate(events=fixtures.candidate().events[:1]))
        _, _, held = self.ingest()
        summary = self.expire(days=60)
        self.assertEqual(summary["expired_content_count"], 1)
        self.assertEqual(self.store.load_content(first.content_id).event_count, 2)
        self.assertEqual(self.store.load_content(latest.content_id).event_count, 1)
        self.assertEqual(self.store.last_accepted()["attempt_no"], latest.attempt_no)
        self.assertTrue(self.store.content_expired(held.content_id))

    def test_shared_content_accepted_by_review_is_protected(self):
        _, _, held = self.ingest()
        self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None, reason="confirmed_duplicates")
        self.assertEqual(self.expire()["eligible_content_count"], 0)
        self.assertEqual(self.store.load_content(held.content_id).event_count, 4)

    def test_shared_recent_quarantine_and_exact_cutoff_are_not_expired(self):
        _, _, held = self.ingest()
        self.assertEqual(self.expire(days=30)["eligible_content_count"], 0)
        self.now += timedelta(days=20)
        self.ingest()
        self.assertEqual(self.expire(days=31)["eligible_content_count"], 0)
        self.assertFalse(self.store.content_expired(held.content_id))

    def test_unreferenced_content_and_in_progress_capture_are_not_expired(self):
        with self.store.transaction():
            self.store.put_content(fixtures.candidate(), created_at=self.now)
        _, capture, held = self.ingest()
        with self.store.transaction() as connection:
            attempt = self.store.begin_attempt(str(uuid4()), origin="live", started_at=self.now)
            digest = fingerprint_snapshot(capture.candidate)
            connection.execute("""UPDATE fetch_attempt SET capture_digest=?,capture_normalization_version=1,
                capture_format_version=1,capture_event_count=?,capture_received_at_utc_us=1
                WHERE attempt_no=?""", (digest.sha256, capture.candidate.event_count, attempt))
        self.assertEqual(self.expire()["eligible_content_count"], 0)
        self.assertFalse(self.store.content_expired(held.content_id))

    def test_fresh_observation_rehydrates_payload_without_inventing_old_observations(self):
        _, capture, held = self.ingest()
        self.expire()
        self.now += timedelta(days=32)
        _, _, new = self.ingest(capture.candidate)
        self.assertEqual(new.content_id, held.content_id)
        self.assertNotEqual(new.observation_uuid, held.observation_uuid)
        self.assertFalse(self.store.content_expired(new.content_id))
        self.assertEqual(canonical_snapshot_bytes(self.store.load_content(new.content_id)),
                         canonical_snapshot_bytes(capture.candidate))
        self.assertEqual((self.store.status()["attempt_count"], self.store.status()["content_count"]), (2, 1))

    def test_rehydration_rollback_keeps_expired_state_and_zero_event_rows(self):
        _, capture, held = self.ingest()
        self.expire()
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.put_content(capture.candidate, created_at=self.now)
                raise RuntimeError("injected")
        self.assertTrue(self.store.content_expired(held.content_id))
        self.assertEqual(self.store.status()["event_row_count"], 0)

    def test_expired_backup_round_trip_and_empty_quarantine_are_supported(self):
        self.ingest(SnapshotCandidate(events=()))
        self.expire()
        report = backup_store(self.directory)
        path = self.directory / "backups" / report["backup_name"]
        restored = restore_backup(path, self.root / "restored")
        self.assertEqual(restored["expired_content_count"], 1)
        with Store(self.root / "restored") as store:
            self.assertEqual(store.status(), self.store.status())
        self.assertEqual(self.expire()["eligible_content_count"], 0)
        with sqlite3.connect(path, autocommit=True) as connection:
            connection.execute("UPDATE snapshot_content SET digest=?", ("0" * 64,))
        with self.assertRaisesRegex(StorageError, "backup_observation_mismatch"):
            verify_backup(path)

    def test_backup_failure_prevents_expiry(self):
        _, _, held = self.ingest()
        before = self.store.status()
        with patch("otter.retention._create_backup", side_effect=StorageError("backup_timeout")):
            with self.assertRaises(StorageError):
                self.expire()
        self.assertEqual(self.store.status(), before)
        self.assertFalse(self.store.content_expired(held.content_id))

    def test_expired_payload_cannot_be_reviewed_even_with_a_regressed_clock(self):
        _, _, held = self.ingest()
        self.expire()
        with self.assertRaisesRegex(StorageError, "content_expired"):
            self.service.approve(held.observation_uuid, expected_baseline_attempt_no=None,
                                 reason="confirmed_duplicates")
        self.assertIsNone(self.store.last_accepted())

    def test_mid_transaction_failure_rolls_back_all_expiry_changes(self):
        _, _, held = self.ingest()
        with self.store.transaction() as connection:
            connection.execute("""CREATE TRIGGER synthetic_fail BEFORE UPDATE OF expired_at_utc_us ON snapshot_content
                BEGIN SELECT RAISE(ABORT,'injected'); END""")
        # Inject after the verified backup, to exercise failure after event deletion.
        from otter.retention import _create_backup
        def backup_then_fault(connection, directory):
            connection.execute("DROP TRIGGER synthetic_fail")
            result = _create_backup(connection, directory)
            connection.execute("""CREATE TRIGGER synthetic_fail BEFORE UPDATE OF expired_at_utc_us ON snapshot_content
                BEGIN SELECT RAISE(ABORT,'injected'); END""")
            return result
        with patch("otter.retention._create_backup", side_effect=backup_then_fault):
            with self.assertRaises(StorageError):
                self.expire()
        self.assertEqual(self.store.load_content(held.content_id).event_count, 4)
        self.assertFalse(self.store.content_expired(held.content_id))

    def test_sql_guards_protect_accepted_rows_and_prevent_accepting_expired_content(self):
        _, _, accepted = self.ingest(fixtures.candidate())
        for sql in ("DELETE FROM snapshot_event WHERE content_id=?",
                    "UPDATE snapshot_event SET title='Changed' WHERE content_id=?",
                    "UPDATE snapshot_content SET expired_at_utc_us=1 WHERE content_id=?"):
            with self.assertRaises(StorageError), self.store.transaction() as connection:
                connection.execute(sql, (accepted.content_id,))
        _, _, held = self.ingest()
        self.expire()
        with self.assertRaises(StorageError), self.store.transaction() as connection:
            connection.execute("UPDATE fetch_attempt SET status='accepted',accepted_at_utc_us=1 WHERE attempt_no=?",
                               (held.attempt_no,))
        self.assertEqual(self.store.load_content(accepted.content_id).event_count, 2)

    def test_cli_preview_policy_validation_and_expired_observation_are_redacted(self):
        _, _, held = self.ingest()
        self.expire()
        output = io.StringIO()
        old = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output):
                code = main(["observation", held.observation_uuid, "--directory", str(self.directory)])
        finally:
            os.umask(old)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["result"]["content_expired"])
        self.assertNotIn("Synthetic", output.getvalue())
        for days in (0, -1, True, 36501):
            with self.assertRaisesRegex(StorageError, "invalid_retention_policy"):
                prune_quarantine(self.directory, older_than_days=days)
