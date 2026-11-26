"""Disposable SQLite migration failures, backup verification and version gates."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otter import migrations
from otter._store_files import DATABASE, StorageError
from otter.storage import Store, initialize_store, migrate_store

import test_m2_storage as fixtures

CURRENT = len(migrations.MIGRATIONS)

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / ".otter-data"
        initialize_store(self.directory, now=fixtures.NOW)

    def connect(self):
        connection = sqlite3.connect(self.directory / DATABASE, autocommit=True)
        self.addCleanup(connection.close)
        return connection

    def test_initialization_failure_publishes_no_partial_database_and_can_retry(self):
        directory = self.directory.parent / "new-store"
        with patch("otter.storage.sync_file", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                initialize_store(directory)
        self.assertFalse((directory / DATABASE).exists())
        initialize_store(directory)
        with Store(directory) as store:
            self.assertEqual(store.status()["schema_version"], CURRENT)

    def test_current_migration_is_a_noop_without_a_backup(self):
        before = (self.directory / DATABASE).read_bytes()
        self.assertFalse(migrate_store(self.directory))
        self.assertEqual((self.directory / DATABASE).read_bytes(), before)
        self.assertFalse((self.directory / "backups").exists())

    def test_pending_upgrade_requires_explicit_command_and_preserves_history(self):
        with Store(self.directory) as store:
            fixtures.seed_observation(store)
            before = store.status()
        second = migrations.Migration(CURRENT + 1, ("CREATE TABLE synthetic_upgrade (value INTEGER)",))
        original = migrations.MIGRATIONS
        with patch("otter.migrations.MIGRATIONS", (*original, second)):
            with self.assertRaisesRegex(StorageError, "schema_migration_required"):
                Store(self.directory)
            self.assertTrue(migrate_store(self.directory, now=fixtures.NOW))
            with Store(self.directory) as store:
                self.assertEqual(store.status()["schema_version"], CURRENT + 1)
                self.assertEqual(store.status()["last_accepted"], before["last_accepted"])
        with self.assertRaisesRegex(StorageError, "schema_newer_than_application"):
            Store(self.directory)

    def test_pre_migration_backup_is_private_independent_and_verified(self):
        with Store(self.directory) as store:
            fixtures.seed_observation(store)
        second = migrations.Migration(CURRENT + 1, ("CREATE TABLE synthetic_upgrade (value INTEGER)",))
        with patch("otter.migrations.MIGRATIONS", (*migrations.MIGRATIONS, second)):
            migrate_store(self.directory)
        backups = list((self.directory / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(backups[0].parent.stat().st_mode & 0o777, 0o700)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(backup.execute("SELECT max(version) FROM schema_migration").fetchone()[0], CURRENT)
            self.assertEqual(backup.execute("SELECT count(*) FROM fetch_attempt WHERE status='accepted'").fetchone()[0], 1)
            self.assertIsNone(backup.execute("SELECT name FROM sqlite_master WHERE name='synthetic_upgrade'").fetchone())

    def test_failed_migration_rolls_back_ddl_and_version(self):
        with Store(self.directory) as store:
            fixtures.seed_observation(store)
            before = store.status()
        broken = migrations.Migration(CURRENT + 1, ("CREATE TABLE rollback_probe (value INTEGER)",
                                           "INSERT INTO no_such_table VALUES (1)"))
        with patch("otter.migrations.MIGRATIONS", (*migrations.MIGRATIONS, broken)):
            with self.assertRaises(StorageError):
                migrate_store(self.directory)
        with Store(self.directory) as store:
            self.assertEqual(store.status(), before)
            self.assertNotIn("rollback_probe", {row["name"] for row in store.schema()})
        self.assertEqual(len(list((self.directory / "backups").glob("*.sqlite3"))), 1)

    def test_backup_failure_prevents_any_migration(self):
        second = migrations.Migration(CURRENT + 1, ("CREATE TABLE forbidden_upgrade (value INTEGER)",))
        with patch("otter.migrations.MIGRATIONS", (*migrations.MIGRATIONS, second)), \
                patch("otter.storage.migration_backup", side_effect=StorageError("injected_backup_failure")):
            with self.assertRaisesRegex(StorageError, "injected_backup_failure"):
                migrate_store(self.directory)
        with Store(self.directory) as store:
            self.assertEqual(store.status()["schema_version"], CURRENT)
            self.assertNotIn("forbidden_upgrade", {row["name"] for row in store.schema()})

    def test_modified_checksum_and_newer_schema_are_not_repaired(self):
        db = self.connect()
        db.execute("UPDATE schema_migration SET checksum=? WHERE version=1", ("0" * 64,))
        for operation in (Store, migrate_store):
            with self.subTest(operation=operation), self.assertRaisesRegex(StorageError, "schema_checksum_mismatch"):
                operation(self.directory)
        db.execute("UPDATE schema_migration SET checksum=? WHERE version=1", (migrations.MIGRATIONS[0].checksum,))
        db.execute("INSERT INTO schema_migration VALUES (?,?,0)", (CURRENT + 1, "0" * 64))
        with self.assertRaisesRegex(StorageError, "schema_newer_than_application"):
            Store(self.directory)

    def test_history_gaps_and_empty_history_are_rejected(self):
        db = self.connect()
        db.execute("DELETE FROM schema_migration")
        with self.assertRaisesRegex(StorageError, "invalid_migration_history"):
            Store(self.directory)
        db.execute("INSERT INTO schema_migration VALUES (?,?,0)", (CURRENT + 2, "0" * 64))
        second = migrations.Migration(CURRENT + 1, ())
        third = migrations.Migration(CURRENT + 2, ())
        with patch("otter.migrations.MIGRATIONS", (*migrations.MIGRATIONS, second, third)):
            with self.assertRaisesRegex(StorageError, "invalid_migration_history"):
                Store(self.directory)

    def test_preexisting_unknown_database_is_not_initialized_by_migrate(self):
        db = self.connect()
        for name in ("store_state", "fetch_attempt", "snapshot_event", "snapshot_content", "schema_migration"):
            db.execute(f"DROP TABLE {name}")
        db.execute("CREATE TABLE unrelated(value TEXT)")
        with self.assertRaisesRegex(StorageError, "unrecognized_store_schema"):
            migrate_store(self.directory)
        self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone())
