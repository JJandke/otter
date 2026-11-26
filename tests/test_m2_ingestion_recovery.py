"""Real SQLite rollback/restart/lock cases for ingestion (no live provider)."""

import json
import select
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from otter import migrations
from otter._store_files import DATABASE, StorageError
from otter.assessment import AssessmentPolicy
from otter.ingestion import (
    CapturedSnapshot, IngestionError, IngestionService, ObservationContext, PendingObservationError,
)
from otter.storage import Store, initialize_store, migrate_store

import test_m2_ingestion as fixtures
import test_m2_assessment as samples
import test_m2_storage as storage_fixtures


class IngestionRecoveryTests(unittest.TestCase):
    setUp = fixtures.IngestionTests.setUp
    ingest = fixtures.IngestionTests.ingest

    def test_fetch_write_failure_exposes_private_receipt_for_persistence_only_retry(self):
        provider = Mock(return_value=CapturedSnapshot(samples.snapshot(future=10), fixtures.NOW))
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(PendingObservationError) as raised:
                self.service.fetch(provider)
        provider.assert_called_once()
        pending = raised.exception
        self.assertEqual(str(pending), "observation_write_pending")
        self.assertNotIn("Synthetic lecture", repr(pending))
        result = self.service.ingest_captured(pending.context, pending.capture)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(self.store.status()["attempt_count"], 1)
        provider.assert_called_once()

    def test_write_failure_leaves_retryable_anchor_and_previous_pointer(self):
        accepted = self.ingest()
        before = self.store.status()
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        capture = CapturedSnapshot(samples.snapshot(future=11), fixtures.NOW)
        put = self.store.put_content
        def fail(*args, **kwargs):
            put(*args, **kwargs)
            raise RuntimeError("PRIVATE-CANARY after content insert")
        with patch.object(self.store, "put_content", side_effect=fail):
            with self.assertRaisesRegex(IngestionError, "observation_processing_failed"):
                self.service.ingest_captured(context, capture)
        row = self.store.attempt(context.observation_uuid)
        self.assertEqual((row["status"], row["reason_code"]), ("started", "observation_write_failed"))
        self.assertIsNotNone(row["capture_digest"])
        self.assertEqual(self.store.status()["content_count"], before["content_count"])
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
        with self.assertRaisesRegex(IngestionError, "observation_id_conflict"):
            self.service.ingest_captured(context, CapturedSnapshot(samples.snapshot(future=12), fixtures.NOW))
        result = self.service.ingest_captured(context, capture)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(self.store.status()["attempt_count"], 2)
        self.assertNotIn(b"PRIVATE-CANARY", (self.directory / DATABASE).read_bytes())

    def test_pointer_failure_rolls_back_final_status_and_content(self):
        accepted = self.ingest()
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        capture = CapturedSnapshot(samples.snapshot(future=11), fixtures.NOW)
        with self.store.transaction() as db:
            db.execute("""CREATE TEMP TRIGGER injected_pointer_failure BEFORE UPDATE ON store_state
                BEGIN SELECT RAISE(ABORT,'injected'); END""")
        with self.assertRaises(StorageError):
            self.service.ingest_captured(context, capture)
        self.assertEqual(self.store.attempt(context.observation_uuid)["status"], "started")
        self.assertEqual(self.store.status()["content_count"], 1)
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted.attempt_no)
        with self.store.transaction() as db:
            db.execute("DROP TRIGGER injected_pointer_failure")
        self.assertEqual(self.service.ingest_captured(context, capture).status, "accepted")

    def test_retry_uses_original_policy_and_receipt_anchor_is_immutable(self):
        self.ingest()
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        capture = CapturedSnapshot(samples.snapshot(future=5), fixtures.NOW)
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(StorageError):
                self.service.ingest_captured(context, capture)
        with self.assertRaises(StorageError):
            with self.store.transaction() as db:
                db.execute("UPDATE fetch_attempt SET future_drop_percent=100 WHERE observation_uuid=?",
                           (context.observation_uuid,))
        other = IngestionService(self.store, clock=lambda: fixtures.NOW, policy=AssessmentPolicy(1, 100))
        result = other.ingest_captured(context, capture)
        self.assertEqual(result.status, "suspicious")
        self.assertEqual(self.store.attempt(context.observation_uuid)["future_drop_percent"], 30)

    def test_new_run_recovers_abandoned_start_without_reusing_old_uuid(self):
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        self.service._start(context)
        provider = Mock(return_value=CapturedSnapshot(samples.snapshot(future=10), fixtures.NOW))
        result = self.service.fetch(provider)
        self.assertEqual(self.service.result(context.observation_uuid).status, "interrupted")
        self.assertNotEqual(result.observation_uuid, context.observation_uuid)
        provider.assert_called_once()
        self.assertEqual(self.service.recover_interrupted(), 0)

    def test_terminal_interrupted_receipt_cannot_be_promoted_by_replay(self):
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), fixtures.NOW)
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(StorageError):
                self.service.ingest_captured(context, capture)
        self.assertEqual(self.service.recover_interrupted(), 1)
        self.assertEqual(self.service.ingest_captured(context, capture).status, "interrupted")
        self.assertIsNone(self.store.last_accepted())

    def test_receipt_retry_survives_a_fresh_process(self):
        context = ObservationContext(str(uuid4()), "live", fixtures.NOW)
        capture = CapturedSnapshot(samples.snapshot(future=10), fixtures.NOW)
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(StorageError):
                self.service.ingest_captured(context, capture)
        script = """
import sys,json
from pathlib import Path
from otter.storage import Store
from otter.ingestion import IngestionService,CapturedSnapshot,ObservationContext
import test_m2_assessment as samples
with Store(Path(sys.argv[1])) as s:
    service=IngestionService(s,clock=lambda:samples.REFERENCE)
    context=ObservationContext(sys.argv[2],'live',samples.REFERENCE)
    print(json.dumps(service.ingest_captured(context,CapturedSnapshot(samples.snapshot(future=10),samples.REFERENCE)).safe_summary()))
"""
        import os
        env = {**os.environ, "PYTHONPATH": "src:tests"}
        result = subprocess.run([sys.executable, "-c", script, str(self.directory), context.observation_uuid],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "accepted")
        self.assertEqual(self.store.status()["attempt_count"], 1)

    def test_process_kill_recovers_without_a_partial_accepted_snapshot(self):
        accepted = self.ingest()
        observation_uuid = str(uuid4())
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
from otter.storage import Store
from otter.ingestion import IngestionService,CapturedSnapshot,ObservationContext
import test_m2_assessment as samples
with Store(Path(sys.argv[1])) as s:
    service=IngestionService(s,clock=lambda:samples.REFERENCE)
    original=s.put_content
    def wait(*args,**kwargs):
        original(*args,**kwargs)
        print('ready',flush=True)
        sys.stdin.read(1)
    with patch.object(s,'put_content',side_effect=wait):
        service.ingest_captured(ObservationContext(sys.argv[2],'live',samples.REFERENCE),
            CapturedSnapshot(samples.snapshot(future=11),samples.REFERENCE))
"""
        import os
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory), observation_uuid],
                                 env={**os.environ, "PYTHONPATH": "src:tests"}, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 10)[0])
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.kill()
            child.wait(timeout=5)
            with Store(self.directory) as reopened:
                service = IngestionService(reopened, clock=lambda: fixtures.NOW)
                self.assertEqual(service.recover_interrupted(), 1)
                self.assertEqual(service.result(observation_uuid).status, "interrupted")
                self.assertEqual(reopened.last_accepted()["attempt_no"], accepted.attempt_no)
                self.assertEqual(reopened.status()["content_count"], 1)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()

    def test_competing_service_cannot_fetch_or_recover_an_active_start(self):
        with Store(self.directory) as other:
            service = IngestionService(other, clock=lambda: fixtures.NOW)
            provider = Mock()
            with self.store.exclusive():
                with self.assertRaisesRegex(StorageError, "store_already_in_use"):
                    service.fetch(provider)
                with self.assertRaisesRegex(StorageError, "store_already_in_use"):
                    service.recover_interrupted()
            provider.assert_not_called()
        self.assertEqual(self.store.status()["attempt_count"], 0)

    def test_real_version_one_upgrade_preserves_existing_data_and_checksum(self):
        directory = self.directory.parent / "version-one"
        with patch("otter.migrations.MIGRATIONS", migrations.MIGRATIONS[:1]):
            initialize_store(directory)
            with Store(directory) as store:
                storage_fixtures.seed_observation(store)
        self.assertEqual(migrations.MIGRATIONS[0].checksum,
                         "2d7140d2c9d89720db5c70623bb42fb7f7b558e9ddc3e2d0e79fddbb3d300efd")
        with self.assertRaisesRegex(StorageError, "schema_migration_required"):
            Store(directory)
        self.assertTrue(migrate_store(directory))
        with Store(directory) as store:
            self.assertEqual(store.status()["schema_version"], 2)
            self.assertEqual(store.status()["content_count"], 1)
            self.assertEqual(store.load_content(store.last_accepted()["content_id"]).event_count, 2)
        backup = next((directory / "backups").glob("*.sqlite3"))
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("SELECT max(version) FROM schema_migration").fetchone()[0], 1)
