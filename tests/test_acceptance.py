"""Offline runner tests: synthetic child JSON and virtual clocks, no account use."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from provadis_m1 import acceptance as a
from provadis_m1.errors import ConfigurationError
from provadis_m1.state import private_state


def payload(command="fetch", *, password=False, ok=True):
    result = {"ok": ok, "command": command,
              "category": "validated_snapshot" if ok else "authentication",
              "reason": "session_requires_interactive_login", "browser_runs": []}
    if ok:
        for _ in range(2 if command == "verify" else 1):
            counts = [2, 2] if command == "verify" else [2]
            result["browser_runs"].append({
                "direct_api_samples": len(counts), "event_counts": counts,
                "diagnostics": {"browser_mode": "headless", "auth_actions":
                                {"password": 1} if password else {"study": 1}}
            })
    return result


class Clock:
    def __init__(self):
        self.t, self.w = 0, 100000
        self.sleeps = []
        self.on_sleep = None

    def advance(self, seconds):
        self.t += seconds
        self.w += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.on_sleep:
            self.on_sleep()
        self.advance(seconds)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.clock = Clock()
        self.calls = []
        self.metadata = {"source_sha256": "source", "python": "3.12.14"}
        self.env_patch = patch.object(a, "environment", return_value=self.metadata)
        self.fingerprint_patch = patch.object(a, "source_fingerprint", return_value="source")
        self.env_patch.start()
        self.fingerprint_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.fingerprint_patch.stop)

    def child(self, argv, timeout):
        command = argv[3]
        state = Path(argv[argv.index("--state-dir") + 1])
        first = not state.exists()
        with private_state(state):
            pass
        self.calls.append((command, argv, self.clock.t))
        self.clock.advance(.1)
        result = payload(command, password=first)
        return a.project_summary(result, command, 0)

    def runner(self, mode="smoke", child=None, **kwargs):
        config = a.Configuration(mode, Path("/private/SECRET-credentials.env"),
                                 output_dir=self.root / ".m1-acceptance", **kwargs)
        directory = a.create_run_directory(config.output_dir)
        return a.Runner(config, directory, child=child or self.child,
                        monotonic=lambda: self.clock.t, wall=lambda: self.clock.w,
                        sleep=self.clock.sleep)

    def test_smoke_uses_fresh_profiles_and_separate_strict_wet_processes(self):
        run = self.runner()
        self.assertEqual(run.execute(), 0)
        self.assertEqual([call[0] for call in self.calls],
                         ["login", "fetch", "fetch", "fetch", "verify", "fetch", "verify"])
        steps = run.report["steps"]
        self.assertEqual(steps[0]["state"], steps[4]["state"])
        self.assertNotEqual(steps[0]["state"], steps[5]["state"])
        self.assertTrue(steps[0]["password_submission_observed"])
        self.assertTrue(steps[5]["password_submission_observed"])
        self.assertFalse(any(s["credentials_authorized"] for s in steps[1:5]))
        self.assertEqual(run.report["credential_submitting_steps"], 2)
        self.assertTrue(all(argv[0] == sys.executable for _, argv, _ in self.calls))
        self.assertNotIn("SECRET", (run.directory / "report.json").read_text())

    def test_multiple_cycles_never_reuse_a_previous_dry_profile(self):
        run = self.runner(fresh_cycles=2, wet_runs=1)
        self.assertEqual(run.execute(), 0)
        fresh = [s["state"] for s in run.report["steps"] if s["password_submission_observed"]]
        self.assertEqual(len(fresh), 4)
        self.assertEqual(len(set(fresh)), 4)

    def test_first_failure_stops_the_run_and_preserves_failed_evidence(self):
        def fail(argv, timeout):
            self.calls.append((argv[3], argv, self.clock.t))
            return a.project_summary(payload(argv[3], ok=False), argv[3], 2)
        run = self.runner(child=fail)
        self.assertEqual(run.execute(), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(run.report["status"], "failed")
        self.assertEqual(run.report["steps"][0]["category"], "authentication")

    def test_success_without_fresh_password_evidence_is_inconclusive(self):
        def no_password(argv, timeout):
            return a.project_summary(payload(argv[3]), argv[3], 0)
        run = self.runner(child=no_password)
        self.assertEqual(run.execute(), 5)
        self.assertEqual(run.report["status"], "inconclusive")
        self.assertEqual(len(run.report["steps"]), 1)

    def test_child_timeout_never_counts_as_validated(self):
        run = self.runner(child=lambda *_: {"ok": False, "category": "runner_timeout", "exit_code": 124})
        self.assertEqual(run.execute(), 1)
        self.assertEqual(run.report["steps"][0]["category"], "runner_timeout")

    def test_idle_holds_profile_lock_and_makes_no_intermediate_browser_calls(self):
        run = self.runner("idle", idle_hours=.01)
        observed = []
        def probe_lock():
            observed.append(len(self.calls))
            self.assertEqual(run.report["waiting"]["requested_seconds"], 36)
            with self.assertRaisesRegex(ConfigurationError, "profile_already_in_use"):
                with private_state(run.state("idle")):
                    self.fail("idle profile lock was not held")
        self.clock.on_sleep = probe_lock
        self.assertEqual(run.execute(), 0)
        self.assertTrue(observed)
        self.assertTrue(all(n == 2 for n in observed))
        self.assertEqual([c[0] for c in self.calls], ["fetch", "verify", "fetch", "verify"])
        self.assertGreaterEqual(run.report["timing"][0]["elapsed_seconds"], 36)
        self.assertTrue(run.report["idle_reuse_without_credentials"])

    def test_automatic_authentication_after_idle_is_recorded_as_reauthentication(self):
        def relogin(argv, timeout):
            result = self.child(argv, timeout)
            if len(self.calls) == 3:
                result = a.project_summary(payload("fetch", password=True), "fetch", 0)
            return result
        run = self.runner("idle", child=relogin, idle_hours=.001)
        self.assertEqual(run.execute(), 0)
        self.assertFalse(run.report["idle_reuse_without_credentials"])
        self.assertTrue(run.report["steps"][2]["credentials_authorized"])

    def test_failed_automatic_recovery_after_idle_cannot_pass_or_fall_back_to_manual(self):
        def failed_recovery(argv, timeout):
            result = self.child(argv, timeout)
            if len(self.calls) == 3:
                return a.project_summary(payload("fetch", ok=False), "fetch", 2)
            return result
        run = self.runner("idle", child=failed_recovery, idle_hours=.001)
        self.assertEqual(run.execute(), 1)
        self.assertEqual(run.report["status"], "failed")
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(any("--headed" in call[1] for call in self.calls))

    def test_active_starts_a_fresh_fetch_at_or_after_the_requested_horizon(self):
        run = self.runner("active", interval_seconds=2, duration_hours=.002)
        self.assertEqual(run.execute(), 0)
        samples = [s for s in run.report["steps"] if s["label"] == "active_fetch"]
        self.assertEqual(len(samples), 4)
        self.assertGreaterEqual(run.report["active_elapsed_seconds"], 7.2)
        self.assertTrue(all(s["credentials_authorized"] for s in samples))

    def test_slow_probe_cannot_replace_a_post_horizon_fresh_process(self):
        def slow(argv, timeout):
            result = self.child(argv, timeout)
            if len(self.calls) == 3:
                self.clock.advance(5)
            return result
        run = self.runner("active", child=slow, interval_seconds=2, duration_hours=.001)
        self.assertEqual(run.execute(), 0)
        starts = [c[2] for c in self.calls[2:-1]]
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[-1] - .2, 3.6)

    def test_sleep_gap_is_inconclusive_and_does_not_trigger_catch_up_bursts(self):
        run = self.runner("active", interval_seconds=10, duration_hours=.01)
        self.clock.on_sleep = lambda: self.clock.advance(60)
        self.assertEqual(run.execute(), 5)
        self.assertEqual(run.report["reason"], "active_schedule_had_gaps")
        self.assertEqual(sum(s["label"] == "active_fetch" for s in run.report["steps"]), 1)

    def test_wall_clock_rollback_fails_instead_of_claiming_idle_duration(self):
        run = self.runner("idle", idle_hours=.001)
        self.clock.on_sleep = lambda: setattr(self.clock, "w", self.clock.w - 100)
        self.assertEqual(run.execute(), 1)
        self.assertEqual(run.report["reason"], "clock_moved_backwards")

    def test_source_changes_during_a_child_run_invalidate_acceptance(self):
        run = self.runner()
        with patch.object(a, "source_fingerprint", side_effect=["source", "changed"]):
            self.assertEqual(run.execute(), 1)
        self.assertEqual(run.report["reason"], "source_changed_during_run")

    def test_ctrl_c_is_recorded_without_a_success_claim(self):
        def interrupt(*_):
            raise KeyboardInterrupt
        run = self.runner(child=interrupt)
        self.assertEqual(run.execute(), 130)
        self.assertEqual(run.report["status"], "interrupted")
        self.assertEqual(run.report["steps"][0]["status"], "running")

    def test_report_and_profile_permissions_and_run_isolation(self):
        first, second = self.runner(), self.runner()
        self.assertNotEqual(first.directory, second.directory)
        self.assertEqual(first.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((first.directory / "report.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(first.directory.glob(".report-*")))


class ProtocolAndProcessTests(unittest.TestCase):
    def test_valid_api_evidence_and_no_raw_text_copying(self):
        data = payload("verify", password=True)
        data.update(secret="SECRET", reason="SECRET")
        data["browser_runs"][0]["diagnostics"]["account"] = "SECRET"
        result = a.project_summary(data, "verify", 0)
        self.assertTrue(result["credential_submission_used"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_success_requires_matching_sample_count_and_browser_lifetimes(self):
        cases = []
        data = payload("verify"); data["browser_runs"].pop(); cases.append(data)
        data = payload("verify"); data["browser_runs"][0]["event_counts"] = [2]; cases.append(data)
        data = payload("verify"); data["browser_runs"][0]["event_counts"][0] = True; cases.append(data)
        data = payload("verify"); data["browser_runs"][0]["diagnostics"]["browser_mode"] = "headed"; cases.append(data)
        data = payload("verify"); data["browser_runs"][0]["diagnostics"]["auth_actions"]["SECRET"] = 1; cases.append(data)
        for data in cases:
            with self.assertRaises(ValueError):
                a.project_summary(data, "verify", 0)
        with self.assertRaises(ValueError):
            a.project_summary(payload(), "fetch", 2)

    def test_unrecognised_child_failure_reason_is_not_persisted(self):
        data = payload(ok=False)
        data["reason"] = "secret_which_looks_like_a_code"
        data["diagnostics"] = {"auth_state": "password", "auth_operation": "click_control",
                               "last_ui_error_kind": "element_detached", "message": "SECRET",
                               "auth_actions": {"password": 1}}
        result = a.project_summary(data, "fetch", 2)
        self.assertEqual(result["reason"], "unclassified_child_failure")
        self.assertEqual(result["auth_state"], "password")
        self.assertTrue(result["credential_submission_used"])
        self.assertEqual(result["failed_auth_actions"], {"password": 1})
        self.assertNotIn("secret_which", json.dumps(result))
        self.assertNotIn("SECRET", json.dumps(result))

    def test_real_subprocess_transport_discards_stderr_and_projects_stdout(self):
        data = payload(); data["unexpected"] = "SECRET"
        # No OTTER/browser execution: this child only prints synthetic JSON.
        script = "import sys; print(" + repr(json.dumps(data)) + "); print('SECRET',file=sys.stderr)"
        result = a.run_child([sys.executable, "-c", script, "fetch"], 5)
        self.assertTrue(result["ok"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_real_child_timeout_is_bounded_and_kills_its_process_group(self):
        result = a.run_child([sys.executable, "-c", "import time; time.sleep(60)", "fetch"], .05)
        self.assertEqual(result, {"ok": False, "category": "runner_timeout", "exit_code": 124})

    def test_graceful_timeout_cleanup_does_not_signal_a_finished_group_again(self):
        process = Mock(pid=12345)
        with patch.object(a.os, "killpg") as kill:
            a.stop_process_group(process)
        self.assertEqual(kill.call_count, 1)
        process.communicate.assert_called_once_with(timeout=5)

    def test_invalid_child_output_is_not_saved_or_returned(self):
        result = a.run_child([sys.executable, "-c", "print('SECRET garbage')", "fetch"], 5)
        self.assertEqual(result["category"], "runner_protocol")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_interrupt_also_terminates_the_child_group(self):
        process = Mock()
        process.communicate.side_effect = KeyboardInterrupt
        with patch.object(a.subprocess, "Popen", return_value=process), \
                patch.object(a, "stop_process_group") as stop:
            with self.assertRaises(KeyboardInterrupt):
                a.run_child([sys.executable, "-m", "provadis_m1", "fetch"], 1)
        stop.assert_called_once_with(process)

    def test_output_directory_refuses_links_shared_paths_and_unmarked_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            shared = root / ".m1-acceptance"
            shared.mkdir(mode=0o700)
            shared.chmod(0o755)
            with self.assertRaises(ConfigurationError):
                a.create_run_directory(shared)
            shared.chmod(0o700)
            (shared / "unrelated").write_text("preserve me")
            with self.assertRaises(ConfigurationError):
                a.create_run_directory(shared)
            self.assertEqual((shared / "unrelated").read_text(), "preserve me")
            link = root / "alias"; link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ConfigurationError):
                a.create_run_directory(link / ".m1-acceptance")

    def test_cli_rejects_invalid_numbers_without_starting_live_tests(self):
        for args in (("--fresh-cycles", "0"), ("--idle-hours", "nan"),
                     ("--interval-seconds", "0"), ("--duration-hours", "inf")):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                a.parser().parse_args(["smoke", "--credentials-file", "/private.env", *args])
