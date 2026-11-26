"""Verified private backup/restore against real disposable SQLite files."""

import contextlib
import io
import json
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from otter import migrations
from otter.__main__ import main
from otter._store_files import DATABASE, StorageError
from otter.backups import backup_store, check_store, restore_backup, verify_backup
from otter.ingestion import CapturedSnapshot, IngestionService, ObservationContext
from otter.models import SnapshotCandidate
from otter.storage import Store, initialize_store, migrate_store
from provadis_m1.errors import InvalidResponse, NetworkError

import test_m2_storage as fixtures


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "source"
        initialize_store(self.directory)
        self.now = fixtures.NOW

    def populate(self):
        with Store(self.directory) as store:
            service = IngestionService(store, clock=lambda: self.now)
            results = []
            for _ in range(2):
                results.append(service.fetch(lambda: CapturedSnapshot(fixtures.candidate(), self.now)))
            results.append(service.fetch(lambda: CapturedSnapshot(SnapshotCandidate(events=()), self.now)))
            results.append(service.fetch(lambda: (_ for _ in ()).throw(InvalidResponse("malformed_json"))))
            results.append(service.fetch(lambda: (_ for _ in ()).throw(NetworkError("network_request_failed"))))
            with self.assertRaises(KeyboardInterrupt):
                service.fetch(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
            interrupted = store._db().execute(
                "SELECT observation_uuid FROM fetch_attempt ORDER BY attempt_no DESC LIMIT 1").fetchone()[0]
            results.append(service.result(interrupted))
            with store.transaction():
                store.begin_attempt(str(uuid4()), origin="live", started_at=self.now)
            return results, store.status()

    def backup(self, **kwargs):
        report = backup_store(self.directory, **kwargs)
        parent = kwargs.get("backup_directory", self.directory / "backups")
        return parent / report["backup_name"], report

    def cli(self, *args):
        output = io.StringIO()
        old = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output):
                code = main(list(args))
        finally:
            os.umask(old)
        return code, json.loads(output.getvalue())

    def test_round_trip_retains_all_observations_receipts_offsets_baseline_and_stream(self):
        observations, status = self.populate()
        source_bytes = (self.directory / DATABASE).read_bytes()
        path, report = self.backup()
        target = self.root / "restored"
        restored = restore_backup(path, target)
        self.assertFalse(restored["migration_required"])
        self.assertEqual(verify_backup(path), {k: v for k, v in report.items() if k != "backup_name"})
        self.assertEqual(check_store(target), verify_backup(path))
        self.assertEqual((self.directory / DATABASE).read_bytes(), source_bytes)
        with Store(self.directory) as source, Store(target) as destination:
            self.assertEqual(destination.status(), status)
            self.assertEqual(destination.accepted_baseline(), source.accepted_baseline())
            for result in observations:
                self.assertEqual(destination.attempt(result.observation_uuid), source.attempt(result.observation_uuid))
        self.assertEqual(report["started_count"], 1)  # Backup never recovers/rewrites it.
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual((target / DATABASE).stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertNotEqual(path.stat().st_ino, (target / DATABASE).stat().st_ino)

    def test_backup_is_independent_of_later_source_writes_and_reopens_in_fresh_process(self):
        self.populate()
        path, report = self.backup()
        with Store(self.directory) as store:
            IngestionService(store, clock=lambda: self.now).fetch(lambda: CapturedSnapshot(fixtures.candidate(), self.now))
        result = subprocess.run([sys.executable, "-m", "otter", "verify-backup", str(path)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["attempt_count"], report["attempt_count"])

    def test_external_backup_directory_has_only_the_projected_database(self):
        self.populate()
        external = self.root / ".otter-backups"
        path, _ = self.backup(backup_directory=external)
        self.assertEqual(list(external.iterdir()), [path])
        self.assertNotIn(b"OTTER_PASSWORD", path.read_bytes())
        self.assertNotIn(b"access_token", path.read_bytes())

    def test_corrupt_backup_does_not_create_restore_destination(self):
        path, _ = self.backup()
        path.write_bytes(b"PRIVATE-CANARY not SQLite")
        target = self.root / "never-created"
        for operation in (lambda: verify_backup(path), lambda: restore_backup(path, target)):
            with self.assertRaises(StorageError) as error:
                operation()
            self.assertNotIn("PRIVATE-CANARY", str(error.exception))
        self.assertFalse(target.exists())
        self.assertEqual(path.read_bytes(), b"PRIVATE-CANARY not SQLite")

    def test_restore_never_overwrites_existing_missing_or_corrupt_original_store(self):
        self.populate()
        path, _ = self.backup()
        original = (self.directory / DATABASE).read_bytes()
        with self.assertRaisesRegex(StorageError, "store_already_exists"):
            restore_backup(path, self.directory)
        self.assertEqual((self.directory / DATABASE).read_bytes(), original)
        (self.directory / DATABASE).write_bytes(b"PRIVATE-CANARY damaged")
        with self.assertRaisesRegex(StorageError, "store_already_exists"):
            restore_backup(path, self.directory)
        (self.directory / DATABASE).unlink()
        (self.directory / (DATABASE + "-journal")).write_bytes(b"PRIVATE-CANARY preserved")
        with self.assertRaisesRegex(StorageError, "restore_destination_not_empty"):
            restore_backup(path, self.directory)
        self.assertFalse((self.directory / DATABASE).exists())

    def test_symlinks_hardlinks_shared_files_and_sidecars_are_rejected(self):
        path, _ = self.backup()
        link = path.parent / "link.sqlite3"
        link.symlink_to(path)
        with self.assertRaises(StorageError):
            verify_backup(link)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(StorageError):
            verify_backup(path)
        link.unlink()
        path.chmod(0o644)
        with self.assertRaises(StorageError):
            verify_backup(path)
        path.chmod(0o600)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            sidecar.touch(mode=0o600)
            with self.assertRaisesRegex(StorageError, "backup_has_sidecars"):
                verify_backup(path)
            sidecar.unlink()

    def test_live_database_hidden_partial_and_non_backup_input_are_rejected(self):
        path, _ = self.backup()
        for name in (".backup-test.partial.sqlite3", "backup.txt"):
            other = path.with_name(name)
            other.write_bytes(path.read_bytes())
            other.chmod(0o600)
            with self.assertRaisesRegex(StorageError, "invalid_backup_file"):
                verify_backup(other)
        with self.assertRaisesRegex(StorageError, "invalid_backup_file"):
            verify_backup(self.directory / DATABASE)

    def test_authentication_paths_and_symlinked_backup_parent_are_rejected_before_creation(self):
        for name in (".m1-state", ".otter-runtime", ".git"):
            parent = self.root / name
            parent.mkdir(mode=0o700)
            target = parent / "backups"
            with self.assertRaises(StorageError):
                self.backup(backup_directory=target)
            self.assertFalse(target.exists())
        link = self.root / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StorageError):
            self.backup(backup_directory=link / "backups")
        with self.assertRaisesRegex(StorageError, "unsafe_backup_directory"):
            self.backup(backup_directory=self.directory / "unexpected")
        self.assertFalse((self.directory / "unexpected").exists())
        path, _ = self.backup()
        alias = self.directory / ".." / "unexpected"
        with self.assertRaisesRegex(StorageError, "unsafe_store_directory"):
            restore_backup(path, alias)
        self.assertFalse((self.root / "unexpected").exists())

    def test_full_verification_detects_tampered_nonbaseline_content_and_extra_schema(self):
        self.populate()
        with Store(self.directory) as store:
            held = IngestionService(store, clock=lambda: self.now).fetch(
                lambda: CapturedSnapshot(SnapshotCandidate(events=fixtures.candidate().events * 2), self.now))
        path, _ = self.backup()
        with sqlite3.connect(path, autocommit=True) as connection:
            # Corrupt an older/quarantined payload, not the accepted baseline.
            connection.execute("UPDATE snapshot_event SET title='Changed' WHERE content_id=? AND ordinal=0",
                               (held.content_id,))
        with self.assertRaisesRegex(StorageError, "content_integrity_failed"):
            verify_backup(path)
        with self.assertRaises(StorageError):
            restore_backup(path, self.root / "rejected")
        path.unlink()
        path, _ = self.backup()
        with sqlite3.connect(path, autocommit=True) as connection:
            # LIKE 'sqlite_%' would accidentally hide this application table.
            connection.execute("CREATE TABLE sqliteSurprise(value TEXT)")
        with self.assertRaisesRegex(StorageError, "backup_schema_mismatch"):
            verify_backup(path)

    def test_checksums_newer_schemas_and_invalid_accepted_pointer_fail_closed(self):
        for fault in ("checksum", "newer", "pointer"):
            path, _ = self.backup()
            with sqlite3.connect(path, autocommit=True) as connection:
                if fault == "checksum":
                    connection.execute("UPDATE schema_migration SET checksum=? WHERE version=1", ("0" * 64,))
                elif fault == "newer":
                    connection.execute("INSERT INTO schema_migration VALUES (?,?,0)", (len(migrations.MIGRATIONS) + 1, "0" * 64))
                else:
                    connection.execute("DROP TRIGGER accepted_pointer_update")
                    connection.execute("UPDATE store_state SET last_accepted_attempt_no=999")
            with self.subTest(fault=fault), self.assertRaises(StorageError):
                verify_backup(path)

    def test_version_two_backup_restores_without_implicit_migration(self):
        older = self.root / "older"
        with patch("otter.migrations.MIGRATIONS", migrations.MIGRATIONS[:2]):
            initialize_store(older)
            with Store(older) as store:
                fixtures.seed_observation(store)
        report = backup_store(older)
        path = older / "backups" / report["backup_name"]
        self.assertEqual(verify_backup(path)["schema_version"], 2)
        target = self.root / "restored-old"
        self.assertTrue(restore_backup(path, target)["migration_required"])
        with self.assertRaisesRegex(StorageError, "schema_migration_required"):
            Store(target)
        self.assertTrue(migrate_store(target))
        with Store(target) as store:
            self.assertEqual(store.status()["attempt_count"], 1)
            self.assertEqual(store.load_content(store.last_accepted()["content_id"]).event_count, 2)

    def test_migration_two_to_three_preserves_checksums_data_and_verified_preupgrade_backup(self):
        older = self.root / "older"
        old_migrations = migrations.MIGRATIONS[:2]
        with patch("otter.migrations.MIGRATIONS", old_migrations):
            initialize_store(older)
            with Store(older) as store:
                fixtures.seed_observation(store)
                previous = store.last_accepted()
        from otter._store_files import integrity_check
        checks = []
        def check_before_publication(connection, **kwargs):
            checks.append(True)
            self.assertFalse(list((older / "backups").glob("pre-migration-*.sqlite3")))
            self.assertEqual(len(list((older / "backups").glob(".migration-*.partial.sqlite3"))), 1)
            integrity_check(connection, **kwargs)
        with patch("otter._store_files.integrity_check", side_effect=check_before_publication):
            self.assertTrue(migrate_store(older))
        self.assertEqual(checks, [True])
        path = next((older / "backups").glob("*.sqlite3"))
        self.assertEqual(verify_backup(path)["schema_version"], 2)
        with Store(older) as store:
            self.assertEqual(store.last_accepted(), previous)
            self.assertEqual(store.status()["expired_content_count"], 0)
            with store._read() as connection:
                self.assertEqual([row[0] for row in connection.execute(
                    "SELECT checksum FROM schema_migration WHERE version<=2 ORDER BY version")],
                    [migration.checksum for migration in old_migrations])

    def test_application_lock_blocks_backup_check_restore_and_prune_in_other_process(self):
        path, _ = self.backup()
        with Store(self.directory) as store, store.exclusive():
            for args in (("backup",), ("check",), ("restore", str(path)), ("prune-quarantine", "--apply")):
                result = subprocess.run([sys.executable, "-m", "otter", *args, "--directory", str(self.directory)],
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 9, result.stderr)
                self.assertEqual(json.loads(result.stdout)["reason"], "store_already_in_use")

    def test_copy_timeout_and_verification_failure_never_publish_backup(self):
        for hook, fault in (("_copy", StorageError("backup_timeout")),
                            ("sync_file", OSError("PRIVATE-CANARY"))):
            with patch("otter.backups." + hook, side_effect=fault):
                with self.assertRaises(StorageError):
                    self.backup()
            self.assertEqual(list((self.directory / "backups").iterdir()), [])
        with patch("otter.backups.monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(StorageError, "backup_timeout"):
                self.backup()
        self.assertEqual(list((self.directory / "backups").iterdir()), [])

    def test_failed_restore_publishes_nothing_and_can_retry(self):
        path, _ = self.backup()
        target = self.root / "restored"
        for hook in ("_copy", "sync_file", "os.link"):
            with patch("otter.backups." + hook, side_effect=OSError("PRIVATE-CANARY")):
                with self.assertRaises(StorageError):
                    restore_backup(path, target)
            self.assertFalse((target / DATABASE).exists())
            self.assertEqual({p.name for p in target.iterdir()}, {".owner", ".lock"})
        restore_backup(path, target)
        self.assertEqual(check_store(target), verify_backup(path))

    def test_killed_backup_keeps_last_published_backup_and_source_usable(self):
        self.populate()
        path, _ = self.backup()
        previous = verify_backup(path)
        script = """
from pathlib import Path
from unittest.mock import patch
from otter.backups import backup_store
import sys
def stop(*args):
    print('ready', flush=True)
    sys.stdin.read(1)
with patch('otter.backups._copy', side_effect=stop):
    backup_store(Path(sys.argv[1]))
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 10)[0])
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()
        self.assertEqual(verify_backup(path), previous)
        self.assertEqual(check_store(self.directory), previous)
        self.assertEqual(len(list(path.parent.glob("snapshot-*.sqlite3"))), 1)
        self.assertEqual(len(list(path.parent.glob(".backup-*.partial.sqlite3"))), 1)
        self.backup()  # Stale unpublished temp does not replace/block a new backup.

    def test_verification_is_read_only_and_never_changes_backup_bytes(self):
        self.populate()
        path, _ = self.backup()
        original, timestamp = path.read_bytes(), path.stat().st_mtime_ns
        verify_backup(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, timestamp)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_killed_restore_does_not_publish_or_touch_original_history(self):
        self.populate()
        path, _ = self.backup()
        before = (self.directory / DATABASE).read_bytes()
        target = self.root / "killed-restore"
        script = """
from pathlib import Path
from unittest.mock import patch
from otter.backups import restore_backup
import sys
def stop(*args):
    print('ready', flush=True)
    sys.stdin.read(1)
with patch('otter.backups._copy', side_effect=stop):
    restore_backup(Path(sys.argv[1]), Path(sys.argv[2]))
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(path), str(target)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 10)[0])
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()
        self.assertFalse((target / DATABASE).exists())
        self.assertEqual((self.directory / DATABASE).read_bytes(), before)
        self.assertEqual(len(list(target.glob(".restore-*.partial.sqlite3"))), 1)
        with self.assertRaisesRegex(StorageError, "restore_destination_not_empty"):
            restore_backup(path, target)
        restore_backup(path, self.root / "fresh-retry")

    def test_cli_commands_are_redacted_and_do_not_authenticate(self):
        self.populate()
        with patch("otter.__main__.M1Provider", side_effect=AssertionError("must not authenticate")):
            code, result = self.cli("backup", "--directory", str(self.directory))
            self.assertEqual(code, 0)
            path = self.directory / "backups" / result["result"]["backup_name"]
            for args in (("verify-backup", str(path)), ("check", "--directory", str(self.directory)),
                         ("restore", str(path), "--directory", str(self.root / "restored"))):
                code, result = self.cli(*args)
                self.assertEqual(code, 0)
                for forbidden in (str(self.root), "Synthetic", "Bearer", "digest"):
                    self.assertNotIn(forbidden, json.dumps(result))
        with patch("otter.__main__.backup_store", side_effect=StorageError("PRIVATE-CANARY")):
            code, result = self.cli("backup", "--directory", str(self.directory))
        self.assertEqual((code, result["reason"]), (9, "storage_failed"))
        self.assertNotIn("PRIVATE-CANARY", json.dumps(result))

    def test_private_api_fields_never_reach_backups_or_cli_diagnostics(self):
        from provadis_m1.validation import parse_candidate_response
        payload = json.loads((Path(__file__).parent / "fixtures" / "valid.json").read_text())
        secret = "PRIVATE-CANARY-NEVER-RETAIN"
        for row in payload["data"]:
            row.update(personid=987654321, email=secret, instructorname=secret,
                       groupname=secret, datasource=secret, access_token=secret)
        candidate = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        with Store(self.directory) as store:
            IngestionService(store, clock=lambda: self.now).fetch(lambda: CapturedSnapshot(candidate, self.now))
        path, _ = self.backup()
        restore_backup(path, self.root / "restored")
        for file in (self.directory / DATABASE, path, self.root / "restored" / DATABASE):
            self.assertNotIn(secret.encode(), file.read_bytes())
            self.assertNotIn(b"987654321", file.read_bytes())
        for args in (("check", "--directory", str(self.directory)), ("verify-backup", str(path))):
            code, result = self.cli(*args)
            self.assertEqual(code, 0)
            self.assertNotIn(secret, json.dumps(result))
