"""File-backed repository tests; final states below are SQL fixtures, not ingestion."""

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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from otter._store_files import DATABASE, StorageError
from otter.migrations import MIGRATIONS
from otter.models import SnapshotCandidate
from otter.normalization import canonical_snapshot_bytes, fingerprint_snapshot, normalize_event
from otter.storage import Store, initialize_store, main
from provadis_m1.validation import parse_candidate_response

NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


def candidate():
    return SnapshotCandidate(events=(
        normalize_event(title="Synthetic B", start=NOW, end=NOW + timedelta(hours=1),
                        building=None, room=None),
        normalize_event(title="Synthetic A", start=NOW + timedelta(days=1),
                        end=NOW + timedelta(days=1, hours=1), building="B", room="R"),
    ))


def seed_observation(store, payload=None, *, status="accepted", origin="live"):
    """Seed final metadata directly to test schema/queries before Issue #7."""
    with store.transaction() as db:
        attempt = store.begin_attempt(str(uuid4()), origin=origin, started_at=NOW)
        content = store.put_content(candidate() if payload is None else payload, created_at=NOW)
        count = db.execute("SELECT event_count FROM snapshot_content WHERE content_id=?", (content,)).fetchone()[0]
        db.execute("""UPDATE fetch_attempt SET status=?,received_at_utc_us=1,
            validated_at_utc_us=2,finished_at_utc_us=3,accepted_at_utc_us=?,
            http_status=200,reported_count=?,content_id=? WHERE attempt_no=?""",
                   (status, 3 if status == "accepted" else None, count, content, attempt))
        if status == "accepted":
            db.execute("UPDATE store_state SET last_accepted_attempt_no=?", (attempt,))
    return attempt, content


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / ".otter-data"
        initialize_store(self.directory, now=NOW)
        self.store = Store(self.directory)
        self.addCleanup(self.store.close)

    def test_initial_schema_connection_and_private_permissions(self):
        status = self.store.status()
        self.assertEqual(status["schema_version"], len(MIGRATIONS))
        self.assertEqual((status["attempt_count"], status["content_count"]), (0, 0))
        self.assertIsNone(status["last_accepted"])
        names = {row["name"] for row in self.store.schema() if row["type"] == "table"}
        self.assertEqual(names, {"schema_migration", "fetch_attempt", "snapshot_content",
                                 "snapshot_event", "store_state"})
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for path in (self.directory / DATABASE, self.directory / ".owner", self.directory / ".lock"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_three_equal_observations_reuse_one_content_set(self):
        ids = [seed_observation(self.store) for _ in range(3)]
        status = self.store.status()
        self.assertEqual(len({a for a, _ in ids}), 3)
        self.assertEqual(len({c for _, c in ids}), 1)
        self.assertEqual((status["attempt_count"], status["content_count"], status["event_row_count"]), (3, 1, 2))
        self.assertEqual(canonical_snapshot_bytes(self.store.load_content(ids[0][1])),
                         canonical_snapshot_bytes(candidate()))

    def test_reordering_and_duplicate_multiplicity(self):
        payload = candidate()
        with self.store.transaction():
            first = self.store.put_content(payload, created_at=NOW)
            same = self.store.put_content(SnapshotCandidate(events=payload.events[::-1]), created_at=NOW)
            double = self.store.put_content(SnapshotCandidate(events=payload.events * 2), created_at=NOW)
        self.assertEqual(first, same)
        self.assertNotEqual(first, double)
        self.assertEqual(self.store.load_content(double).event_count, 4)

    def test_last_successful_and_last_accepted_are_different_queries(self):
        accepted, _ = seed_observation(self.store)
        suspicious, _ = seed_observation(self.store, SnapshotCandidate(events=()), status="suspicious")
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted)
        self.assertEqual(self.store.last_successful_fetch()["attempt_no"], suspicious)
        imported, _ = seed_observation(self.store, origin="import")
        self.assertEqual(self.store.last_accepted()["attempt_no"], imported)
        self.assertEqual(self.store.last_successful_fetch()["attempt_no"], suspicious)
        self.assertEqual(canonical_snapshot_bytes(self.store.accepted_baseline().candidate),
                         canonical_snapshot_bytes(candidate()))

    def test_content_and_observations_survive_a_fresh_process(self):
        _, content = seed_observation(self.store)
        script = ("from pathlib import Path; from otter.storage import Store; "
                  "import json,sys; "
                  "s=Store(Path(sys.argv[1])); "
                  "print(json.dumps([s.status()['attempt_count'],s.load_content(int(sys.argv[2])).event_count])); s.close()")
        result = subprocess.run([sys.executable, "-c", script, str(self.directory), str(content)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [1, 2])

    def test_killed_writer_preserves_the_last_committed_observation(self):
        seed_observation(self.store)
        before = self.store.status()
        script = """
from pathlib import Path
import sys
from datetime import datetime,timezone,timedelta
from otter.storage import Store
from otter.normalization import normalize_event
from otter.models import SnapshotCandidate
with Store(Path(sys.argv[1])) as s:
    with s.transaction():
        t=datetime(2027,1,1,tzinfo=timezone.utc)
        e=normalize_event(title='Uncommitted',start=t,end=t+timedelta(hours=1),building=None,room=None)
        s.put_content(SnapshotCandidate(events=(e,)),created_at=t)
        print('ready',flush=True)
        sys.stdin.read(1)
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(self.directory)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 10)[0])
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.kill()
            child.wait(timeout=5)
            with Store(self.directory) as restarted:
                self.assertEqual(restarted.status(), before)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()

    def test_actual_parser_projection_keeps_offsets_without_private_fields(self):
        payload = json.loads((Path(__file__).parent / "fixtures" / "valid.json").read_bytes())
        for row in payload["data"]:
            row.update(email="PRIVATE-CANARY@example.invalid", personid=987654321,
                       instructorname="PRIVATE-CANARY", groupname="PRIVATE-CANARY")
        parsed = parse_candidate_response(200, "application/json", json.dumps(payload).encode())
        with self.store.transaction():
            content = self.store.put_content(parsed, created_at=NOW)
        self.assertEqual(canonical_snapshot_bytes(self.store.load_content(content)),
                         canonical_snapshot_bytes(parsed))
        self.assertNotIn(b"PRIVATE-CANARY", (self.directory / DATABASE).read_bytes())
        self.assertNotIn(b"987654321", (self.directory / DATABASE).read_bytes())

    def test_exception_rolls_back_content_attempt_and_pointer(self):
        accepted, _ = seed_observation(self.store)
        before = self.store.status()
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with self.store.transaction() as db:
                number = self.store.begin_attempt(str(uuid4()), origin="live", started_at=NOW)
                changed = SnapshotCandidate(events=(replace(candidate().events[0], title="Different"),))
                content = self.store.put_content(changed, created_at=NOW)
                db.execute("""UPDATE fetch_attempt SET status='accepted',content_id=?,
                    received_at_utc_us=1,validated_at_utc_us=2,finished_at_utc_us=3,
                    accepted_at_utc_us=3,reported_count=1 WHERE attempt_no=?""", (content, number))
                db.execute("UPDATE store_state SET last_accepted_attempt_no=?", (number,))
                raise RuntimeError("injected")
        self.assertEqual(self.store.status(), before)
        self.assertEqual(self.store.last_accepted()["attempt_no"], accepted)

    def test_keyboard_interrupt_rolls_back_and_releases_lock(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.store.transaction():
                self.store.put_content(candidate(), created_at=NOW)
                raise KeyboardInterrupt()
        with Store(self.directory) as other, other.transaction():
            self.assertEqual(other.status()["content_count"], 0)

    def test_writes_require_transaction_and_nesting_is_rejected(self):
        with self.assertRaisesRegex(StorageError, "store_write_requires_transaction"):
            self.store.put_content(candidate(), created_at=NOW)
        with self.store.exclusive(), self.store.transaction():
            with self.assertRaisesRegex(StorageError, "nested_store_transaction"):
                with self.store.transaction():
                    pass

    def test_unique_observation_uuid_is_enforced_without_overwriting(self):
        observation = str(uuid4())
        with self.store.transaction():
            number = self.store.begin_attempt(observation, origin="live", started_at=NOW)
        with self.assertRaisesRegex(StorageError, "storage_constraint_failed"):
            with self.store.transaction():
                self.store.begin_attempt(observation, origin="live", started_at=NOW)
        self.assertEqual(self.store.status()["attempt_count"], 1)
        self.assertIsNotNone(number)

    def test_foreign_keys_status_intervals_and_pointer_constraints(self):
        with self.store.transaction():
            number = self.store.begin_attempt(str(uuid4()), origin="live", started_at=NOW)
            content = self.store.put_content(candidate(), created_at=NOW)
        statements = [
            ("UPDATE store_state SET last_accepted_attempt_no=?", (number,)),
            ("UPDATE fetch_attempt SET status='accepted' WHERE attempt_no=?", (number,)),
            ("UPDATE fetch_attempt SET baseline_attempt_no=999999 WHERE attempt_no=?", (number,)),
            ("UPDATE snapshot_event SET end_utc_us=start_utc_us WHERE content_id=?", (content,)),
            ("UPDATE snapshot_content SET event_count=-1 WHERE content_id=?", (content,)),
        ]
        for sql, values in statements:
            with self.subTest(sql=sql), self.assertRaises(StorageError):
                with self.store.transaction() as db:
                    db.execute(sql, values)

    def test_accepted_records_cannot_be_changed_deleted_or_pointer_cleared(self):
        number, _ = seed_observation(self.store)
        for sql in ("UPDATE fetch_attempt SET reported_count=100 WHERE attempt_no=?",
                    "DELETE FROM fetch_attempt WHERE attempt_no=?"):
            with self.subTest(sql=sql), self.assertRaises(StorageError):
                with self.store.transaction() as db:
                    db.execute(sql, (number,))
        with self.assertRaises(StorageError):
            with self.store.transaction() as db:
                db.execute("UPDATE store_state SET last_accepted_attempt_no=NULL")

    def test_direct_sql_tampering_is_detected_on_content_read(self):
        _, content = seed_observation(self.store)
        with self.store.transaction() as db:
            db.execute("UPDATE snapshot_event SET title='Tampered' WHERE content_id=? AND ordinal=0", (content,))
        with self.assertRaisesRegex(StorageError, "content_integrity_failed"):
            self.store.load_content(content)

    def test_hash_collision_does_not_overwrite_prior_rows(self):
        _, content = seed_observation(self.store)
        original = fingerprint_snapshot(candidate())
        changed = SnapshotCandidate(events=(replace(candidate().events[0], title="Different"),))
        with patch("otter.storage.fingerprint_snapshot", return_value=original):
            with self.assertRaisesRegex(StorageError, "content_digest_conflict"):
                with self.store.transaction():
                    self.store.put_content(changed, created_at=NOW)
        self.assertEqual(self.store.load_content(content).event_count, 2)

    def test_manually_forged_models_are_not_persisted(self):
        original = candidate().events[0]
        for changes in ({"title": " "}, {"start_utc_us": True}, {"room": " "},
                        {"end_utc_us": original.start_utc_us}, {"start_offset_seconds": 86400}):
            with self.subTest(changes=changes), self.assertRaises(StorageError):
                with self.store.transaction():
                    self.store.put_content(SnapshotCandidate(events=(replace(original, **changes),)), created_at=NOW)
        self.assertEqual(self.store.status()["content_count"], 0)

    def test_schema_and_status_do_not_contain_timetable_details(self):
        seed_observation(self.store)
        output = json.dumps(self.store.status()) + json.dumps(self.store.schema())
        self.assertNotIn("Synthetic A", output)
        self.assertNotIn("Synthetic B", output)
        self.assertNotIn("Authorization", output)

    def test_missing_database_does_not_reinitialize_store(self):
        self.store.close()
        path = self.directory / DATABASE
        path.rename(self.directory / "preserved.sqlite3")
        with self.assertRaises(StorageError):
            Store(self.directory)
        self.assertFalse(path.exists())
        self.assertTrue((self.directory / "preserved.sqlite3").exists())

    def test_corruption_is_preserved_and_errors_do_not_echo_file_bytes(self):
        self.store.close()
        path = self.directory / DATABASE
        path.write_bytes(b"PRIVATE-CANARY not a database")
        with self.assertRaises(StorageError) as raised:
            Store(self.directory)
        self.assertNotIn("PRIVATE-CANARY", str(raised.exception))
        self.assertEqual(path.read_bytes(), b"PRIVATE-CANARY not a database")

    def test_reinitialization_refuses_to_replace_database(self):
        before = (self.directory / DATABASE).read_bytes()
        with self.assertRaisesRegex(StorageError, "store_already_exists"):
            initialize_store(self.directory)
        self.assertEqual((self.directory / DATABASE).read_bytes(), before)

    def test_permissions_symlinks_hardlinks_and_unmarked_files_are_rejected(self):
        self.directory.chmod(0o755)
        with self.assertRaises(StorageError):
            Store(self.directory)
        self.directory.chmod(0o700)
        path = self.directory / DATABASE
        path.chmod(0o644)
        with self.assertRaises(StorageError):
            Store(self.directory)
        path.chmod(0o600)
        linked = self.directory.parent / "linked.sqlite3"
        os.link(path, linked)
        with self.assertRaises(StorageError):
            Store(self.directory)
        linked.unlink()
        link = self.directory.parent / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StorageError):
            Store(link)
        unknown = self.directory.parent / "unknown"
        unknown.mkdir(mode=0o700)
        (unknown / "keep").write_text("synthetic")
        with self.assertRaises(StorageError):
            initialize_store(unknown)
        self.assertFalse((unknown / DATABASE).exists())

    def test_authentication_profile_directory_is_not_repurposed(self):
        for name in (".m1-state", ".m1-acceptance", ".m1-gui", "profiles"):
            profile = self.directory.parent / name
            with self.subTest(name=name), self.assertRaises(StorageError):
                initialize_store(profile)
            self.assertFalse(profile.exists())

    def test_second_writer_is_rejected_and_lock_is_released(self):
        with Store(self.directory) as other:
            with self.store.exclusive():
                with self.assertRaisesRegex(StorageError, "store_already_in_use"):
                    with other.transaction():
                        pass
            with other.transaction():
                other.put_content(candidate(), created_at=NOW)

    def test_external_sqlite_writer_has_a_bounded_busy_failure(self):
        self.store._db().execute("PRAGMA busy_timeout=50")  # Short test budget only.
        external = sqlite3.connect(self.directory / DATABASE, autocommit=True)
        try:
            external.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(StorageError, "storage_busy"):
                with self.store.transaction():
                    pass
            external.execute("ROLLBACK")
            with self.store.transaction():
                self.store.put_content(candidate(), created_at=NOW)
        finally:
            external.close()

    def test_application_lock_applies_across_processes(self):
        script = """
from pathlib import Path
import sys
from otter.storage import Store,StorageError
try:
    with Store(Path(sys.argv[1])) as s, s.transaction():
        pass
except StorageError as e:
    print(e.reason)
    sys.exit(1)
"""
        with self.store.exclusive():
            result = subprocess.run([sys.executable, "-c", script, str(self.directory)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "store_already_in_use")

    def test_reported_count_must_match_linked_content(self):
        with self.assertRaises(StorageError):
            with self.store.transaction() as db:
                number = self.store.begin_attempt(str(uuid4()), origin="live", started_at=NOW)
                content = self.store.put_content(candidate(), created_at=NOW)
                db.execute("""UPDATE fetch_attempt SET status='accepted',content_id=?,
                    received_at_utc_us=1,validated_at_utc_us=2,finished_at_utc_us=3,
                    accepted_at_utc_us=3,reported_count=999 WHERE attempt_no=?""", (content, number))
        self.assertEqual(self.store.status()["attempt_count"], 0)

    def test_cli_init_status_schema_and_failure_are_redacted(self):
        for command in ("status", "schema", "migrate"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([command, "--directory", str(self.directory)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["status", "--directory", str(self.directory / "PRIVATE-CANARY")])
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE-CANARY", output.getvalue())
