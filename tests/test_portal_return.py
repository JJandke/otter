"""Regression for the 24 November SSO return/reappearing-login failure.

Controls come from the existing sanitized captures. Callback/render transitions
and Playwright failures are simulated; this is not live Microsoft authentication.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_auth_runner as runner
from auth_support import Locator, Page
from provadis_m1.auth_flow import B2C, PORTAL, AuthFlow
from provadis_m1.errors import BootstrapError
from provadis_m1.navigation_log import NavigationLog


class PortalReturnTests(unittest.TestCase):
    invoke = runner.RunnerTests.invoke

    def test_ssoreturn_does_not_click_disappearing_login_button_and_validates_api(self):
        # The failing log's prefix: student -> login -> B2C -> callback's
        # temporary login screen -> no recognised controls -> dashboard.
        rows = runner.plan(runner.DRY[:3], "DryLogin") + [
            ("2_Hochschulportal", PORTAL, "WetLogin"),
            (None, PORTAL, "WetLogin"),
        ] + runner.plan(runner.DRY[-3:], "DryLogin")
        original_wait, original_click = runner.TestPage.wait_for_function, Locator.click

        def callback_render(page, *args, **kwargs):
            if page.index in (3, 4):
                page.pending = False
                page.index += 1
                page.show()  # Portal-owned callback completion, without a tool click.
                return
            return original_wait(page, *args, **kwargs)

        def disappearing_button(locator, **kwargs):
            if locator.page.index == 3:
                raise runner.BrowserError("Element is not attached to the DOM: SECRET")
            return original_click(locator, **kwargs)

        with patch.object(runner.TestPage, "wait_for_function", callback_render), \
                patch.object(Locator, "click", disappearing_button):
            result = self.invoke([rows])
        self.assertEqual(result["event_counts"], [2])
        self.assertEqual(result["diagnostics"]["auth_actions"]["portal_login"], 1)
        self.context.close.assert_called_once()

    def test_reappearing_entry_controls_wait_instead_of_replaying_sso(self):
        for fixture, host, state in (
            ("DryLogin-1_Hochschulportal.html", PORTAL, "student"),
            ("DryLogin-2_Hochschulportal.html", PORTAL, "portal_login"),
            ("DryLogin-3_Anmeldung.html", B2C, "b2c_microsoft"),
        ):
            with self.subTest(state=state):
                flow = AuthFlow()
                first = Page(fixture, host + "/")
                self.assertTrue(flow.advance(first, now=1, timeout_ms=1000))
                flow.advance(Page(None, PORTAL + "/"), now=2, timeout_ms=1000)
                returned = Page(fixture, host + "/")
                self.assertFalse(flow.advance(returned, now=3, timeout_ms=1000))
                self.assertEqual(returned.clicks, [])
                self.assertEqual(flow.actions[state], 1)

    def test_provider_alias_cannot_restart_the_same_attempt(self):
        flow = AuthFlow()
        page = Page("DryLogin-3_Anmeldung.html", B2C + "/")
        flow.advance(page, now=1, timeout_ms=1000)
        page.document.nodes = [n for n in page.document.nodes
                               if n.text.strip() != "mit meinem Microsoft-Konto anmelden"]
        self.assertFalse(flow.advance(page, now=2, timeout_ms=1000))
        self.assertEqual(len(page.clicks), 1)

    def test_explicit_401_recovery_can_start_one_new_entry_sequence(self):
        flow = AuthFlow()
        for cycle in range(2):
            page = Page("DryLogin-2_Hochschulportal.html", PORTAL + "/login/student")
            self.assertTrue(flow.advance(page, now=cycle, timeout_ms=1000))
            if cycle == 0:
                flow.after_expiry()
        self.assertEqual(flow.actions["portal_login"], 2)
        flow.after_expiry()
        with self.assertRaisesRegex(BootstrapError, "transition_limit_portal_login"):
            flow.advance(page, now=3, timeout_ms=1000)

    def test_unfinished_return_remains_a_bounded_failure_without_api_success(self):
        rows = runner.plan(runner.DRY[:3], "DryLogin") + [
            ("2_Hochschulportal", PORTAL, "WetLogin"),
        ]
        with self.assertRaisesRegex(BootstrapError, "authentication_timeout_portal_login"):
            self.invoke([rows], bootstrap_seconds=1)
        self.context.request.get.assert_not_called()
        self.assertEqual(sum(n.text.strip() == "Jetzt anmelden" for n in self.page.clicks), 1)

    def test_suppression_logs_once_without_changing_the_existing_state_deadline(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            flow = AuthFlow(trace=log, state_seconds=2)
            page = Page("DryLogin-2_Hochschulportal.html", PORTAL + "/login/student")
            flow.advance(page, now=0, timeout_ms=1000)
            flow.advance(Page(None, PORTAL + "/"), now=1, timeout_ms=1000)
            for now in (2, 2.5, 3):
                flow.advance(page, now=now, timeout_ms=1000)
            with self.assertRaisesRegex(BootstrapError, "state_timeout_portal_login"):
                flow.advance(page, now=4, timeout_ms=1000)
            rows = [json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertEqual(sum(r["event"] == "auth_action_suppressed" for r in rows), 1)

    def test_failed_operation_and_error_category_are_recorded_without_raw_error(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            flow = AuthFlow(trace=log)
            page = Page("DryLogin-2_Hochschulportal.html", PORTAL + "/login/student")
            with patch.object(Locator, "click", side_effect=runner.BrowserError(
                    "Element is not attached to the DOM; SECRET token person@example.invalid")):
                with self.assertRaises(runner.BrowserError) as raised:
                    flow.advance(page, now=1, timeout_ms=1000)
            flow.record_ui_error(raised.exception, runner.BrowserTimeout)
            row = json.loads(log.path.read_text().splitlines()[-1])
            combined = json.dumps(flow.diagnostics()) + log.path.read_text()
        self.assertEqual(row["operation"], "click_control")
        self.assertEqual(row["error_kind"], "element_detached")
        self.assertNotIn("SECRET", combined)
        self.assertNotIn("person@example.invalid", combined)

    def test_cleanup_error_does_not_replace_the_action_failure(self):
        flow = AuthFlow()
        page = Page("DryLogin-2_Hochschulportal.html", PORTAL + "/login/student")
        with patch.object(Locator, "click", side_effect=runner.BrowserError("element is detached")), \
                patch.object(Locator, "dispose", side_effect=runner.BrowserError("cleanup")):
            with self.assertRaisesRegex(runner.BrowserError, "element is detached"):
                flow.advance(page, now=1, timeout_ms=1000)

    def test_error_categories_are_fixed_and_do_not_retain_exception_details(self):
        cases = (
            (runner.BrowserTimeout("SECRET"), "timeout"),
            (runner.BrowserError("Execution context was destroyed: SECRET"), "context_destroyed"),
            (runner.BrowserError("Cannot find context with specified id SECRET"), "context_destroyed"),
            (runner.BrowserError("Target page, context or browser has been closed SECRET"), "browser_closed"),
            (runner.BrowserError("SECRET unknown failure"), "other"),
        )
        for error, expected in cases:
            flow = AuthFlow()
            self.assertEqual(flow.record_ui_error(error, runner.BrowserTimeout), expected)
            self.assertNotIn("SECRET", json.dumps(flow.diagnostics()) + repr(flow))

    def test_runner_reports_the_operation_and_preserves_the_nonzero_failure(self):
        with patch.object(Locator, "click", side_effect=runner.BrowserError(
                "Element is not attached to the DOM: SECRET")):
            with self.assertRaises(BootstrapError) as raised:
                self.invoke([runner.plan(runner.WET, "WetLogin")])
        self.assertEqual(raised.exception.reason, "authentication_ui_operation_failed")
        self.assertEqual(raised.exception.diagnostics["auth_operation"], "click_control")
        self.assertEqual(raised.exception.diagnostics["last_ui_error_kind"], "element_detached")
        self.assertNotIn("SECRET", json.dumps(raised.exception.diagnostics))

    def test_new_log_fields_reject_unrecognised_values(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            for kwargs in ({"operation": "SECRET"}, {"error_kind": "SECRET"}):
                with self.assertRaises(ValueError):
                    log("auth_ui_error", **kwargs)
            self.assertNotIn("SECRET", log.path.read_text())
