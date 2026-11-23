import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provadis_m1.__main__ import main
from provadis_m1.errors import AuthenticationError, BootstrapError, NetworkError, SuspiciousResponse


class CLITests(unittest.TestCase):
    def invoke(self, command="fetch", side_effect=None, return_value=None, extra=()):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve() / ".m1-state"
            with patch.dict(os.environ, {"DEBUG": "", "PWDEBUG": "", "SSLKEYLOGFILE": ""}):
                with patch("provadis_m1.__main__.browser_run", side_effect=side_effect,
                           return_value=return_value) as run:
                    with contextlib.redirect_stdout(output):
                        previous = os.umask(0o077)
                        try:
                            code = main([command, "--state-dir", str(state), *extra])
                        finally:
                            os.umask(previous)
        return code, json.loads(output.getvalue()), run

    def test_error_classes_have_nonzero_exit_and_safe_output(self):
        for error, code in ((AuthenticationError(), 2), (NetworkError(), 3),
                            (SuspiciousResponse(), 5), (RuntimeError("SECRET"), 6)):
            with self.subTest(code=code):
                actual, result, _ = self.invoke(side_effect=error)
                self.assertEqual(actual, code)
                self.assertFalse(result["ok"])
                self.assertNotIn("SECRET", json.dumps(result))

    def test_verify_requires_two_browser_lifetimes(self):
        code, result, run = self.invoke("verify", return_value={"event_counts": [2, 2]})
        self.assertEqual(code, 0)
        self.assertTrue(result["restart_reuse_demonstrated"])
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(not c.kwargs["interactive"] for c in run.call_args_list))
        self.assertTrue(all(c.kwargs["samples"] == 2 for c in run.call_args_list))

    def test_failed_restart_never_reports_success(self):
        code, result, _ = self.invoke("verify", side_effect=[{"event_counts": [2, 2]},
                                                           AuthenticationError()])
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["browser_runs"]), 1)
        self.assertNotIn("restart_reuse_demonstrated", result)

    def test_bootstrap_diagnostics_reach_safe_cli_output(self):
        error = BootstrapError()
        error.diagnostics = {"final_location": "portal", "portal_api_requests_observed": 0}
        code, result, _ = self.invoke(side_effect=error)
        self.assertEqual(code, 8)
        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostics"], error.diagnostics)
        self.assertEqual(len(result["state_id"]), 12)
        self.assertNotIn(".m1-state", json.dumps(result))

    def test_verify_cannot_be_mistaken_for_a_headed_diagnostic(self):
        code, result, run = self.invoke("verify", extra=("--headed",))
        self.assertEqual(code, 6)
        self.assertFalse(result["ok"])
        self.assertNotIn("unattended_retrieval_demonstrated", result)
        run.assert_not_called()

    def test_headed_fetch_is_passed_to_browser(self):
        code, _, run = self.invoke(return_value={"event_counts": [2]}, extra=("--headed",))
        self.assertEqual(code, 0)
        self.assertTrue(run.call_args.kwargs["headed"])
