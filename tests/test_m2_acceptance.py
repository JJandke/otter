"""Composed M2 acceptance journeys with fake transport and real private SQLite.

These exercise existing application interfaces, not a second implementation.
Every child process is offline. No university credentials or browser are used.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_auth_runner as runner
from otter.__main__ import main
from otter._store_files import DATABASE, StorageError
from otter.backups import backup_store, check_store, restore_backup, verify_backup
from otter.ingestion import CapturedSnapshot, IngestionService, ObservationContext
from otter.m1_adapter import M1Provider
from otter.normalization import EPOCH, canonical_snapshot_bytes
from otter.retention import prune_quarantine
from otter.storage import Store, initialize_store
from provadis_m1.validation import parse_candidate_response

NOW = datetime(2027, 3, 26, 8, tzinfo=timezone.utc)
PRIVATE = "SYNTHETIC-PRIVATE-FIELD-ONLY"


def row(index, start, end):
    offset = timezone(timedelta(hours=1 if index % 2 else 2))
    result = {"eventtitle": "Repeated synthetic lecture", "startdate": start.astimezone(offset).isoformat(),
              "enddate": end.astimezone(offset).isoformat(), "email": PRIVATE, "personid": 987654321,
              "instructorname": PRIVATE, "groupname": PRIVATE, "datasource": PRIVATE, "unknown": PRIVATE}
    # All three no-known-location representations must survive the whole journey.
    if index % 3:
        result.update(buildingname=None if index % 3 == 1 else "  ", roomname="")
    return result


def envelope(rows):
    return json.dumps({"ok": True, "message": PRIVATE, "message2": None,
                       "count": len(rows), "data": rows}).encode()


class M2AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otter-m2-acceptance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.state = self.root / "runtime" / ".m1-state"
        initialize_store(self.data)
        self.now = NOW
        self.environment = patch.dict(os.environ, {"DEBUG": "", "PWDEBUG": "", "SSLKEYLOGFILE": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fetch(self, rows, *, directory=None, status=200, expected=0):
        directory = self.data if directory is None else directory
        self.harness = runner.RunnerTests()
        output, errors = io.StringIO(), io.StringIO()
        previous_umask = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                    patch("otter.__main__.IngestionService", side_effect=lambda store, **kw:
                          IngestionService(store, clock=lambda: self.now, **kw)), \
                    patch("otter.__main__.M1Provider", side_effect=lambda *args, **kw:
                          M1Provider(*args, clock=lambda: self.now, **kw)):
                code = self.harness.invoke([runner.plan(runner.WET, "WetLogin")], body=envelope(rows),
                    statuses=(status,), operation=lambda: main([
                        "fetch", "--directory", str(directory), "--state-dir", str(self.state)]))
        finally:
            os.umask(previous_umask)
        self.assertEqual(code, expected, output.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.harness.context.request.get.assert_called_once()
        self.harness.context.close.assert_called_once()
        self.assert_redacted(output.getvalue())
        return json.loads(output.getvalue())

    def command(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-m", "otter", *map(str, args)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assert_redacted(result.stdout)
        return json.loads(result.stdout)["result"]

    def assert_redacted(self, output):
        for forbidden in (PRIVATE, "987654321", "Repeated synthetic lecture", str(self.root), "Bearer", "capture_digest"):
            self.assertNotIn(forbidden, output)

    def assert_private_bytes(self, *files):
        for file in files:
            self.assertNotIn(PRIVATE.encode(), file.read_bytes())
            self.assertNotIn(b"987654321", file.read_bytes())

    def test_semester_fetch_failures_restart_retention_and_restore_preserve_accepted_history(self):
        soon = [row(i, NOW - timedelta(minutes=30 - i), NOW + timedelta(minutes=10 + i)) for i in range(10)]
        future = [row(i + 10, NOW + timedelta(days=i + 1), NOW + timedelta(days=i + 1, hours=1)) for i in range(7)]
        old = row(99, datetime(2023, 1, 1, 8, tzinfo=timezone.utc), datetime(2023, 1, 1, 9, tzinfo=timezone.utc))
        first = self.fetch([old, *soon, *future])["result"]["observation"]
        self.now += timedelta(hours=2)
        progressed = self.fetch(future)["result"]["observation"]
        self.assertEqual(progressed["status"], "accepted")  # Total falls 18 -> 7; unfinished counts stay 7.
        semester = future + [row(i + 100, NOW + timedelta(days=i + 30), NOW + timedelta(days=i + 30, hours=1))
                             for i in range(43)]
        grown = self.fetch(semester)["result"]["observation"]
        repeated = self.fetch(semester[::-1])["result"]["observation"]
        self.assertEqual(grown["content_id"], repeated["content_id"])
        self.assertNotEqual(grown["observation_uuid"], repeated["observation_uuid"])
        self.assertIn("future_event_count_increased", grown["information"])
        self.fetch(semester[:25], expected=5)
        self.fetch([], expected=5)
        malformed = [*semester, {"startdate": NOW.isoformat()}]
        self.fetch(malformed, expected=4)  # A bad final row rejects the complete response.
        self.fetch(semester, status=503, expected=7)
        status = self.command("status", "--directory", self.data)
        self.assertEqual((status["attempt_count"], status["content_count"]), (8, 5))
        self.assertEqual(status["last_accepted"]["observation_uuid"], repeated["observation_uuid"])
        with Store(self.data) as store:
            initial = store.load_content(first["content_id"])
            self.assertEqual(initial.event_count, 18)
            self.assertTrue(any((EPOCH + timedelta(microseconds=event.end_utc_us)).year == 2023 for event in initial.events))
            self.assertTrue(all(event.building is None and event.room is None for event in initial.events))
            self.assertEqual({event.start_offset_seconds for event in initial.events}, {3600, 7200})
            accepted = [store.attempt(item["observation_uuid"]) for item in (first, progressed, grown, repeated)]
            original = canonical_snapshot_bytes(initial)
        expired = prune_quarantine(self.data, apply=True, now=self.now + timedelta(days=31))
        self.assertEqual(expired["expired_content_count"], 2)
        report = backup_store(self.data)
        backup = self.data / "backups" / report["backup_name"]
        restored = self.root / "restored"
        restore_backup(backup, restored)
        restored_status = self.command("status", "--directory", restored)
        self.assertEqual((restored_status["attempt_count"], restored_status["expired_content_count"]), (8, 2))
        self.assertEqual(restored_status["last_accepted"], status["last_accepted"])
        self.assertEqual(check_store(restored), verify_backup(backup))
        with Store(restored) as store:
            for observation in accepted:
                self.assertEqual(store.attempt(observation["observation_uuid"]), observation)
            self.assertEqual(canonical_snapshot_bytes(store.load_content(first["content_id"])), original)
        self.assert_private_bytes(self.data / DATABASE, backup, restored / DATABASE)

    def test_expired_import_replay_after_restore_keeps_uuid_count_and_payload_expiry(self):
        rows = [row(i, NOW + timedelta(days=i + 1), NOW + timedelta(days=i + 1, hours=1)) for i in range(2)]
        file = self.root / "synthetic-input.json"
        file.write_bytes(envelope(rows))
        accepted_id, held_id = str(uuid4()), str(uuid4())
        self.command("import-json", file, "--directory", self.data, "--observation-id", accepted_id)
        file.write_bytes(envelope(rows * 2))
        held = self.command("import-json", file, "--directory", self.data, "--observation-id", held_id, expected=5)
        prune_quarantine(self.data, apply=True, now=datetime.now(timezone.utc) + timedelta(days=31))
        report = self.command("backup", "--directory", self.data)
        backup = self.data / "backups" / report["backup_name"]
        restored = self.root / "restored"
        self.command("restore", backup, "--directory", restored)
        replay = self.command("import-json", file, "--directory", restored, "--observation-id", held_id, expected=5)
        self.assertEqual(replay, held)
        status = self.command("status", "--directory", restored)
        self.assertEqual((status["attempt_count"], status["expired_content_count"]), (2, 1))
        self.assertEqual(status["last_accepted"]["observation_uuid"], accepted_id)
        new = self.command("import-json", file, "--directory", restored,
                           "--observation-id", str(uuid4()), expected=5)["observation"]
        self.assertEqual(new["content_id"], held["observation"]["content_id"])
        self.assertNotEqual(new["observation_uuid"], held_id)
        self.assertFalse(new["eligible_for_sync"])
        status = self.command("status", "--directory", restored)
        self.assertEqual((status["attempt_count"], status["content_count"], status["expired_content_count"]), (3, 2, 0))
        self.assertEqual(status["last_accepted"]["observation_uuid"], accepted_id)
        self.assert_private_bytes(self.data / DATABASE, backup, restored / DATABASE)

    def test_pending_live_write_survives_backup_restore_and_recovery_without_refetching_old_uuid(self):
        rows = [row(i, NOW + timedelta(days=i + 1), NOW + timedelta(days=i + 1, hours=1)) for i in range(3)]
        accepted = self.fetch(rows[:2])["result"]["observation"]
        with patch.object(Store, "put_content", side_effect=StorageError("storage_io_failed")):
            pending = self.fetch(rows, expected=9)
        identifier = pending["observation_uuid"]
        report = backup_store(self.data)
        self.assertEqual((report["attempt_count"], report["started_count"]), (2, 1))
        restored = self.root / "restored"
        backup = self.data / "backups" / report["backup_name"]
        restore_backup(backup, restored)
        self.assertEqual(self.command("recover", "--directory", restored)["recovered_attempts"], 1)
        candidate = parse_candidate_response(200, "application/json", envelope(rows))
        with Store(restored) as store:
            result = IngestionService(store, clock=lambda: self.now).ingest_captured(
                ObservationContext(identifier, "live", self.now), CapturedSnapshot(candidate, self.now))
            self.assertEqual(result.status, "interrupted")
            self.assertFalse(result.eligible_for_sync)
            self.assertEqual(store.last_accepted()["observation_uuid"], accepted["observation_uuid"])
        fresh = self.fetch(rows, directory=restored)["result"]["observation"]
        self.assertNotEqual(fresh["observation_uuid"], identifier)
        self.assertTrue(fresh["eligible_for_sync"])
        status = self.command("status", "--directory", restored)
        self.assertEqual(status["attempt_count"], 3)
        self.assertEqual(status["last_accepted"]["observation_uuid"], fresh["observation_uuid"])
        # Restoring never edits the preserved source, including its pending start.
        with Store(self.data) as store:
            self.assertEqual(store.attempt(identifier)["status"], "started")
            self.assertEqual(store.last_accepted()["observation_uuid"], accepted["observation_uuid"])
        self.assert_private_bytes(self.data / DATABASE, backup, restored / DATABASE)
