"""M1 adapter contract/path/projection tests; no real browser or credentials."""

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from otter.m1_adapter import FetchOptions, M1Provider
from provadis_m1.errors import ConfigurationError
from provadis_m1.state import private_state
from provadis_m1.validation import parse_candidate_response

VALID = (Path(__file__).parent / "fixtures" / "valid.json").read_bytes()
NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.options = FetchOptions(state_dir=self.root / "runtime" / ".m1-state")
        self.env = patch.dict(os.environ, {"DEBUG": "", "PWDEBUG": "", "SSLKEYLOGFILE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def provider(self, options=None):
        return M1Provider(options or self.options, database_directory=self.root / "data", clock=lambda: NOW)

    def browser(self, **kwargs):
        self.assertEqual((kwargs["samples"], kwargs["sample_interval"], kwargs["interactive"]), (1, 0, False))
        self.assertIs(kwargs["snapshot_validator"], parse_candidate_response)
        kwargs["on_snapshot"](parse_candidate_response(200, "application/json", VALID))
        return {"direct_api_samples": 1, "event_counts": [2], "PRIVATE-CANARY": "ignored",
                "diagnostics": {"direct_api_attempts": 1, "direct_api_transport_failures": 0,
                                "page_error_count": 0, "PRIVATE-CANARY": "ignored",
                                "auth_actions": {"email": 0, "password": 0}}}

    def test_opt_in_contract_and_allowlisted_evidence(self):
        provider = self.provider()
        with patch("otter.m1_adapter.browser_run", side_effect=lambda _profile, **kw: self.browser(**kw)) as run:
            capture = provider()
        self.assertEqual(capture.received_at, NOW)
        self.assertEqual(capture.candidate.event_count, 2)
        self.assertNotIn("PRIVATE-CANARY", str(provider.evidence))
        self.assertFalse(provider.evidence["credential_attempted"])
        self.assertIsNone(run.call_args.kwargs["credentials"])
        self.assertEqual(self.options.state_dir.parent.stat().st_mode & 0o777, 0o700)

    def test_profile_lock_is_released_before_candidate_is_returned(self):
        with patch("otter.m1_adapter.browser_run", side_effect=lambda _profile, **kw: self.browser(**kw)):
            self.provider()()
        with private_state(self.options.state_dir):
            pass

    def test_existing_profile_lock_prevents_browser_launch(self):
        self.options.state_dir.parent.mkdir(mode=0o700)
        with private_state(self.options.state_dir), patch("otter.m1_adapter.browser_run") as run:
            with self.assertRaisesRegex(ConfigurationError, "profile_already_in_use"):
                self.provider()()
        run.assert_not_called()

    def test_missing_multiple_and_mismatched_handoffs_fail_closed(self):
        candidate = parse_candidate_response(200, "application/json", VALID)
        for kind in ("missing", "multiple", "wrong_type", "wrong_count", "boolean_samples"):
            def broken(_profile, **kwargs):
                if kind == "wrong_type":
                    kwargs["on_snapshot"](object())
                if kind != "missing":
                    kwargs["on_snapshot"](candidate)
                if kind == "multiple":
                    kwargs["on_snapshot"](candidate)
                return {"direct_api_samples": True if kind == "boolean_samples" else 1,
                        "event_counts": [99] if kind == "wrong_count" else [2]}
            with self.subTest(kind=kind), patch("otter.m1_adapter.browser_run", side_effect=broken):
                with self.assertRaisesRegex(ConfigurationError, "candidate_handoff_invalid"):
                    self.provider()()

    def test_unsafe_layouts_are_rejected_without_creating_state(self):
        for path in (self.root / "data" / ".m1-state", self.root / ".m1-acceptance" / ".m1-state",
                     self.root / ".git" / ".m1-state", self.root / "not-a-state"):
            with self.subTest(path=path), self.assertRaises(ConfigurationError):
                self.provider(replace(self.options, state_dir=path))
            self.assertFalse(path.exists())

    def test_symlinked_parent_and_shared_writable_parent_are_rejected(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ConfigurationError):
            self.provider(replace(self.options, state_dir=alias / ".m1-state"))
        target.chmod(0o777)
        with patch("otter.m1_adapter.browser_run") as browser:
            with self.assertRaisesRegex(ConfigurationError, "unsafe_state_parent"):
                self.provider(replace(self.options, state_dir=target / ".m1-state"))()
        browser.assert_not_called()

    def test_invalid_configuration_is_checked_before_credential_loading(self):
        for options in (replace(self.options, decline_passkey=True),
                        replace(self.options, request_seconds=0),
                        replace(self.options, headed="PRIVATE-CANARY")):
            with self.subTest(options=options), patch("otter.m1_adapter.load_credentials") as loader:
                with self.assertRaises(ConfigurationError):
                    self.provider(options)
            loader.assert_not_called()

    def test_state_option_paths_do_not_appear_in_repr(self):
        options = replace(self.options, credentials_file=Path("PRIVATE-CANARY.env"))
        self.assertNotIn("PRIVATE-CANARY", repr(options))
        self.assertNotIn(str(self.root), repr(options))
