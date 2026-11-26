"""Real M2 CLI/M1 pipeline with HTML-backed fake Playwright and file SQLite."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import test_auth_runner as runner
from otter.__main__ import main
from otter._store_files import StorageError
from otter.ingestion import IngestionError, IngestionService, ObservationContext
from otter.storage import Store, initialize_store
from provadis_m1.errors import ConfigurationError


class M2CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "private path with spaces"
        self.root.mkdir(mode=0o700)
        self.data = self.root / "data"
        self.state = self.root / "runtime" / ".m1-state"
        self.file = self.root / "input.json"
        self.file.write_bytes(runner.VALID)
        initialize_store(self.data)
        self.env = patch.dict(os.environ, {"DEBUG": "", "PWDEBUG": "", "SSLKEYLOGFILE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, command, *args, live=False, statuses=(200,), body=runner.VALID, dry=False):
        argv = [command, "--directory", str(self.data), *args]
        output, errors = io.StringIO(), io.StringIO()
        old_umask = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                if live:
                    self.harness = runner.RunnerTests()
                    plan = runner.plan(runner.DRY, "DryLogin") if dry else runner.plan(runner.WET, "WetLogin")
                    code = self.harness.invoke([plan], body=body, statuses=statuses, operation=lambda: main(argv))
                else:
                    code = main(argv)
        finally:
            os.umask(old_umask)
        self.assertEqual(errors.getvalue(), "")
        return code, json.loads(output.getvalue())

    def fetch(self, *args, **kwargs):
        return self.invoke("fetch", "--state-dir", str(self.state), *args, live=True, **kwargs)

    def import_file(self, observation_id=None, *args):
        return self.invoke("import-json", str(self.file), "--observation-id", observation_id or str(uuid4()), *args)

    def test_actual_m1_to_database_pipeline_is_one_observation_and_redacted(self):
        code, result = self.fetch()
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        observation = result["result"]["observation"]
        self.assertTrue(observation["eligible_for_sync"])
        self.assertEqual(result["result"]["fetch"]["direct_api_samples"], 1)
        self.harness.context.request.get.assert_called_once()
        self.harness.context.close.assert_called_once()
        with Store(self.data) as store:
            self.assertEqual(store.status()["attempt_count"], 1)
            self.assertEqual(store.load_content(observation["content_id"]).event_count, 2)
        output = json.dumps(result)
        for forbidden in ("Synthetic lecture", "Example building", "example@example.invalid", "Bearer", str(self.state)):
            self.assertNotIn(forbidden, output)

    def test_browser_is_closed_before_database_content_write(self):
        original = Store.put_content
        checks = []
        def put(store, *args, **kwargs):
            checks.append(self.harness.context.close.call_count)
            return original(store, *args, **kwargs)
        with patch.object(Store, "put_content", put):
            self.assertEqual(self.fetch()[0], 0)
        self.assertEqual(checks, [1])

    def test_separate_cli_invocations_reuse_content_with_distinct_observations(self):
        first = self.fetch()[1]["result"]["observation"]
        second = self.fetch()[1]["result"]["observation"]
        self.assertNotEqual(first["observation_uuid"], second["observation_uuid"])
        self.assertEqual(first["content_id"], second["content_id"])
        with Store(self.data) as store:
            self.assertEqual((store.status()["attempt_count"], store.status()["content_count"]), (2, 1))

    def test_explicit_credentials_use_existing_dry_authentication_only(self):
        credentials = self.root / "credentials.env"
        credentials.write_text("OTTER_USERNAME=synthetic@example.invalid\nOTTER_PASSWORD=SYNTHETIC-SECRET\n")
        credentials.chmod(0o600)
        code, result = self.fetch("--credentials-file", str(credentials), "--decline-passkey", dry=True)
        self.assertEqual(code, 0)
        self.assertTrue(result["result"]["fetch"]["credential_attempted"])
        self.assertTrue(result["result"]["fetch"]["credential_submission_observed"])
        self.assertNotIn("SYNTHETIC-SECRET", json.dumps(result))
        self.assertNotIn(b"SYNTHETIC-SECRET", (self.data / "otter.sqlite3").read_bytes())

    def test_wet_fetch_never_loads_credentials_implicitly(self):
        with patch("otter.m1_adapter.load_credentials") as loader:
            self.assertEqual(self.fetch()[0], 0)
        loader.assert_not_called()

    def test_navigation_log_is_opt_in_private_and_redacted(self):
        code, _ = self.fetch("--navigation-log")
        self.assertEqual(code, 0)
        logs = list(self.state.glob("navigation-*.jsonl"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
        for forbidden in ("Synthetic lecture", "example@example.invalid", "Bearer SYNTHETIC"):
            self.assertNotIn(forbidden, logs[0].read_text())

    def test_401_recovery_produces_one_observation_not_two(self):
        code, result = self.fetch(statuses=(401, 200))
        self.assertEqual(code, 0)
        self.assertEqual(self.harness.context.request.get.call_count, 2)
        with Store(self.data) as store:
            self.assertEqual(store.status()["attempt_count"], 1)

    def test_http_failures_do_not_replace_accepted_data(self):
        accepted = self.fetch()[1]["result"]["observation"]
        for status, expected in ((403, 2), (503, 7)):
            code, result = self.fetch(statuses=(status,))
            self.assertEqual(code, expected)
            self.assertFalse(result["ok"])
            self.harness.context.request.get.assert_called_once()
            with Store(self.data) as store:
                self.assertEqual(store.last_accepted()["attempt_no"], accepted["attempt_no"])

    def test_malformed_and_empty_api_results_preserve_accepted_pointer(self):
        accepted = self.fetch()[1]["result"]["observation"]
        for body, expected, status in ((b"malformed PRIVATE-CANARY", 4, "invalid"),
                                       (b'{"ok":true,"data":[],"count":0}', 5, "suspicious")):
            code, result = self.fetch(body=body)
            self.assertEqual(code, expected)
            self.assertEqual(result["result"]["observation"]["status"], status)
            with Store(self.data) as store:
                self.assertEqual(store.last_accepted()["attempt_no"], accepted["attempt_no"])
            self.assertNotIn("PRIVATE-CANARY", json.dumps(result))

    def test_database_failure_cannot_trigger_another_fetch(self):
        with patch.object(Store, "put_content", side_effect=StorageError("PRIVATE-CANARY")):
            code, result = self.fetch()
        self.assertEqual((code, result["reason"]), (9, "observation_write_pending"))
        self.harness.context.request.get.assert_called_once()
        self.assertNotIn("PRIVATE-CANARY", json.dumps(result))
        with Store(self.data) as store:
            self.assertIsNone(store.last_accepted())
            self.assertEqual(store.attempt(result["observation_uuid"])["status"], "started")

    def test_missing_store_fails_before_browser_credentials_or_profile_access(self):
        self.data = self.root / "not-initialized"
        with patch("otter.__main__.M1Provider") as provider, patch("otter.m1_adapter.load_credentials") as loader:
            code, result = self.invoke("fetch", "--state-dir", str(self.state))
        self.assertEqual(code, 9)
        provider.assert_not_called()
        loader.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertFalse(self.data.exists())

    def test_debug_env_and_invalid_options_fail_before_browser(self):
        for flags in (("--decline-passkey",), ("--request-seconds", "0"), ("--state-seconds", "1801")):
            with patch("otter.__main__.M1Provider") as provider:
                self.assertEqual(self.invoke("fetch", *flags)[0], 6)
            provider.assert_not_called()
        with patch.dict(os.environ, {"DEBUG": "1"}), patch("otter.__main__.M1Provider") as provider:
            self.assertEqual(self.invoke("fetch")[0], 6)
        provider.assert_not_called()

    def test_profile_and_database_overlap_is_rejected(self):
        with patch("otter.m1_adapter.browser_run") as browser:
            code, result = self.invoke("fetch", "--state-dir", str(self.data / ".m1-state"))
        self.assertEqual((code, result["reason"]), (6, "database_and_profile_must_be_separate"))
        browser.assert_not_called()
        self.assertFalse((self.data / ".m1-state").exists())

    def test_file_import_replay_is_idempotent_and_excluded_from_sync(self):
        observation_id = str(uuid4())
        with patch("otter.__main__.M1Provider") as provider:
            first = self.import_file(observation_id)
            second = self.import_file(observation_id)
        provider.assert_not_called()
        self.assertEqual(first[0], 0)
        self.assertEqual(first[1]["result"], second[1]["result"])
        self.assertFalse(first[1]["result"]["observation"]["eligible_for_sync"])
        with Store(self.data) as store:
            self.assertEqual((store.status()["attempt_count"], store.status()["content_count"]), (1, 1))
            self.assertIsNone(store.last_successful_fetch())

    def test_import_conflict_cannot_rewrite_existing_observation(self):
        observation_id = str(uuid4())
        self.assertEqual(self.import_file(observation_id)[0], 0)
        payload = json.loads(runner.VALID)
        payload["data"][0]["eventtitle"] = "Changed"
        self.file.write_text(json.dumps(payload))
        code, result = self.import_file(observation_id)
        self.assertEqual((code, result["reason"]), (10, "observation_id_conflict"))
        with Store(self.data) as store:
            self.assertEqual(store.status()["attempt_count"], 1)

    def test_invalid_import_is_recorded_and_requires_new_id_after_correction(self):
        observation_id = str(uuid4())
        self.file.write_bytes(b"bad PRIVATE-CANARY")
        code, result = self.import_file(observation_id)
        self.assertEqual(code, 4)
        self.assertEqual(result["result"]["observation"]["status"], "invalid")
        self.file.write_bytes(runner.VALID)
        self.assertEqual(self.import_file(observation_id)[0], 10)
        self.assertEqual(self.import_file()[0], 0)
        self.assertNotIn(b"PRIVATE-CANARY", (self.data / "otter.sqlite3").read_bytes())

    def test_replay_read_failure_does_not_change_previous_outcome(self):
        observation_id = str(uuid4())
        self.import_file(observation_id)
        self.file.unlink()
        self.assertEqual(self.import_file(observation_id)[0], 6)
        with Store(self.data) as store:
            self.assertEqual(store.attempt(observation_id)["status"], "accepted")

    def test_import_rejects_symlink_fifo_and_oversized_input(self):
        original = self.root / "real.json"
        self.file.rename(original)
        self.file.symlink_to(original)
        self.assertEqual(self.import_file()[0], 6)
        self.file.unlink()
        os.mkfifo(self.file)
        self.assertEqual(self.import_file()[0], 6)
        self.file.unlink()
        with self.file.open("wb") as file:
            file.truncate(8 * 1024 * 1024 + 1)
        self.assertEqual(self.import_file()[0], 4)

    def test_review_is_read_only_until_explicit_approval(self):
        self.file.write_text('{"ok":true,"data":[],"count":0}')
        observation_id = str(uuid4())
        self.assertEqual(self.import_file(observation_id)[0], 5)
        code, preview = self.invoke("review", observation_id)
        self.assertEqual(code, 0)
        self.assertEqual(preview["result"]["observation"]["status"], "suspicious")
        self.assertIsNone(preview["result"]["baseline_attempt_no"])
        self.assertEqual(self.invoke("review", observation_id, "--reason", "confirmed_empty")[0], 6)
        code, result = self.invoke("review", observation_id, "--approve", "--expected-baseline", "none",
                                   "--reason", "confirmed_empty")
        self.assertEqual(code, 0)
        self.assertTrue(result["result"]["observation"]["reviewed"])
        self.assertFalse(result["result"]["observation"]["eligible_for_sync"])

    def test_observation_status_schema_and_recovery_require_no_browser(self):
        observation_id = str(uuid4())
        self.import_file(observation_id)
        with Store(self.data) as store:
            service = IngestionService(store)
            from datetime import datetime, timezone
            service._start(ObservationContext(str(uuid4()), "import", datetime.now(timezone.utc)))
        with patch("otter.__main__.M1Provider") as provider:
            self.assertEqual(self.invoke("observation", observation_id)[0], 0)
            self.assertEqual(self.invoke("status")[0], 0)
            self.assertEqual(self.invoke("schema")[0], 0)
            code, result = self.invoke("recover")
        provider.assert_not_called()
        self.assertEqual((code, result["result"]["recovered_attempts"]), (0, 1))

    def test_policy_arguments_are_validated_and_persisted(self):
        code, result = self.import_file(None, "--minimum-future-drop", "2", "--future-drop-percent", "50")
        self.assertEqual(code, 0)
        observation_id = result["result"]["observation"]["observation_uuid"]
        preview = self.invoke("observation", observation_id)[1]["result"]
        self.assertEqual((preview["minimum_future_drop"], preview["future_drop_percent"]), (2, 50))
        self.assertEqual(self.import_file(None, "--future-drop-percent", "101")[0], 6)

    def test_parser_errors_and_unexpected_exception_messages_are_redacted(self):
        for argv in (("fetch", "--password", "PRIVATE-CANARY"),
                     ("import-json", "PRIVATE-CANARY"), ("verify", "PRIVATE-CANARY")):
            code, result = self.invoke(*argv)
            self.assertEqual(code, 6)
            self.assertNotIn("PRIVATE-CANARY", json.dumps(result))
        for error, code in ((RuntimeError("PRIVATE-CANARY"), 6),
                            (StorageError("PRIVATE-CANARY"), 9),
                            (IngestionError("PRIVATE-CANARY"), 10),
                            (ConfigurationError("PRIVATE-CANARY"), 6)):
            with patch("otter.__main__._execute", side_effect=error):
                actual, result = self.invoke("status")
            self.assertEqual(actual, code)
            self.assertNotIn("PRIVATE-CANARY", json.dumps(result))

    def test_new_cli_in_a_fresh_python_process_can_read_committed_import(self):
        self.import_file()
        result = subprocess.run([sys.executable, "-m", "otter", "status", "--directory", str(self.data)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["attempt_count"], 1)
