"""HTML-backed mocked navigation/API tests; no real Microsoft JavaScript."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from auth_support import Page
from test_auth_flow import CREDS, DRY
from provadis_m1.auth_flow import FIDO, PORTAL
from provadis_m1.browser import API_URL, browser_run
from provadis_m1.errors import AuthenticationError, BootstrapError, InvalidResponse

VALID = (Path(__file__).parent / "fixtures" / "valid.json").read_bytes()
WET = [(f"{i}_Hochschulportal", PORTAL, state) for i, state in enumerate(
    ("student", "portal_login", "study", "schedule", "timetable"), 1)]


class BrowserError(Exception):
    pass


class BrowserTimeout(BrowserError):
    pass


class TestPage(Page):
    def __init__(self, plans, context, emit_api=True, portal_statuses=(200,)):
        super().__init__(None, "about:blank")
        self.context, self.plans, self.emit_api = context, plans, emit_api
        self.plan = []
        self.portal_statuses, self.api_count = portal_statuses, 0
        self.index, self.starts = 0, 0
        self.pending, self.closed = False, False

    def on(self, *args):
        pass

    def is_closed(self):
        return self.closed

    def goto(self, *args, **kwargs):
        self.plan = self.plans[min(self.starts, len(self.plans) - 1)]
        self.starts += 1
        self.index = 0
        self.show()

    def show(self):
        filename, host, prefix = self.plan[self.index]
        self.url = host if "/common/bridge/fido" in host else host + "/"
        self.load(f"{prefix}-{filename}.html" if filename else None)
        if filename and filename in ("11_Hochschulportal", "5_Hochschulportal") and self.emit_api:
            request = Mock(url=API_URL, method="GET")
            request.header_value.return_value = "Bearer SYNTHETIC"
            self.context.events["request"](request)
            status = self.portal_statuses[min(self.api_count, len(self.portal_statuses) - 1)]
            self.api_count += 1
            self.context.events["response"](Mock(url=API_URL, request=request, status=status))

    def on_click(self):
        self.pending = True

    def evaluate(self, *args):
        return [1, self.index, "/"]

    def wait_for_function(self, *args, **kwargs):
        if self.pending:
            self.pending = False
            self.index += 1
            self.show()
        else:
            raise BrowserTimeout()


def plan(rows, prefix):
    return [(filename, host, prefix) for filename, host, _ in rows]


class RunnerTests(unittest.TestCase):
    def invoke(self, plans, *, credentials=None, statuses=(200,), body=VALID,
               emit_api=True, bootstrap_seconds=2, keep=False,
               failure_diagram=None, portal_statuses=(200,)):
        context = Mock()
        context.events = {}
        context.on.side_effect = lambda name, callback: context.events.update({name: callback})
        page = TestPage(plans, context, emit_api, portal_statuses)
        context.pages = [page]
        responses = [Mock(url=API_URL, status=status, headers={"content-type": "application/json"},
                          body=Mock(return_value=body)) for status in statuses]
        context.request.get.side_effect = responses
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = context
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        module = types.ModuleType("playwright.sync_api")
        module.Error, module.TimeoutError = BrowserError, BrowserTimeout
        module.sync_playwright = Mock(return_value=manager)
        self.context, self.page = context, page
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict("sys.modules", {"playwright.sync_api": module}), \
                    patch("provadis_m1.browser.capture_failure", return_value={"report": "safe.json"},
                          side_effect=failure_diagram):
                return browser_run(Path(temp) / "profile", interactive=False,
                                   headed=keep, credentials=credentials,
                                   bootstrap_seconds=bootstrap_seconds,
                                   request_seconds=1, samples=1, sample_interval=0,
                                   keep_open_on_error=keep)

    def test_dry_login_reaches_and_validates_api(self):
        result = self.invoke([plan(DRY, "DryLogin")], credentials=CREDS)
        self.assertEqual(result["event_counts"], [2])
        self.assertEqual(result["diagnostics"]["auth_actions"]["password"], 1)
        self.context.close.assert_called_once()

    def test_wet_login_skips_microsoft_and_validates_api(self):
        result = self.invoke([plan(WET, "WetLogin")])
        self.assertEqual(result["direct_api_samples"], 1)
        self.assertNotIn("email", result["diagnostics"]["auth_actions"])

    def test_directly_authenticated_session_needs_no_ui_actions(self):
        result = self.invoke([plan(DRY[-1:], "DryLogin")])
        self.assertEqual(result["diagnostics"]["auth_actions"], {})

    def test_expired_direct_request_uses_one_ui_reauthentication(self):
        result = self.invoke([plan(WET, "WetLogin"), plan(DRY, "DryLogin")],
                             credentials=CREDS, statuses=(401, 200))
        self.assertTrue(result["diagnostics"]["session_reauthentication"])
        self.assertEqual(self.page.starts, 2)
        self.assertEqual(self.context.request.get.call_count, 2)

    def test_repeated_401_exits_and_does_not_loop(self):
        with self.assertRaises(AuthenticationError):
            self.invoke([plan(WET, "WetLogin")], statuses=(401, 401))
        self.assertEqual(self.page.starts, 2)
        self.context.close.assert_called_once()

    def test_403_does_not_trigger_automatic_login(self):
        with self.assertRaises(AuthenticationError):
            self.invoke([plan(WET, "WetLogin")], statuses=(403,))
        self.assertEqual(self.page.starts, 1)

    def test_expired_without_credentials_reports_interactive_login_required(self):
        with self.assertRaisesRegex(AuthenticationError, "session_requires_interactive_login"):
            self.invoke([plan(WET, "WetLogin"), plan(DRY, "DryLogin")], statuses=(401,))
        self.assertEqual(self.page.starts, 2)

    def test_invalid_api_is_not_success_even_on_timetable_page(self):
        with self.assertRaises(InvalidResponse):
            self.invoke([plan(WET, "WetLogin")], body=b"{}")

    def test_missing_api_response_causes_bounded_timeout(self):
        with self.assertRaisesRegex(BootstrapError, "authentication_timeout"):
            self.invoke([plan(DRY[-1:], "DryLogin")], emit_api=False, bootstrap_seconds=0)
        self.context.request.get.assert_not_called()
        self.context.close.assert_called_once()

    def test_native_passkey_headless_timeout_is_not_success(self):
        with self.assertRaises(BootstrapError):
            self.invoke([[(None, FIDO + "/common/bridge/fido", "DryLogin")]],
                        emit_api=False, bootstrap_seconds=0)
        self.context.request.get.assert_not_called()

    def test_failure_keeps_original_error_when_diagnostics_fail(self):
        with self.assertRaisesRegex(InvalidResponse, "missing_envelope_fields") as raised:
            self.invoke([plan(WET, "WetLogin")], body=b"{}",
                        failure_diagram=OSError("SECRET"))
        self.assertTrue(raised.exception.diagnostics["failure_artifacts_unavailable"])
        self.assertNotIn("SECRET", json.dumps(raised.exception.diagnostics))

    def test_explicit_debug_mode_waits_for_window_close_and_retains_error(self):
        # This wait is mocked; no real browser is launched or left open.
        with patch.object(TestPage, "wait_for_event", create=True) as wait:
            with self.assertRaises(InvalidResponse):
                self.invoke([plan(WET, "WetLogin")], body=b"{}", keep=True)
            wait.assert_called_once_with("close", timeout=0)
        self.context.close.assert_called_once()

    def test_portal_401_on_timetable_falls_back_before_any_direct_request(self):
        result = self.invoke([plan(DRY[-1:], "DryLogin"), plan(DRY, "DryLogin")],
                             credentials=CREDS, portal_statuses=(401, 200))
        self.assertEqual(self.page.starts, 2)
        self.assertEqual(result["event_counts"], [2])
        self.context.request.get.assert_called_once()

    def test_successful_page_without_valid_api_never_returns_success(self):
        with self.assertRaises(InvalidResponse):
            self.invoke([plan(DRY[-1:], "DryLogin")], body=b"[]")
        self.context.close.assert_called_once()

    def test_portal_can_renew_stale_token_without_a_navigation_restart(self):
        original = TestPage.wait_for_function
        renewed = False
        def renew(page, *args, **kwargs):
            nonlocal renewed
            if page.api_count and not renewed:
                renewed = True
                request = Mock(url=API_URL, method="GET")
                request.header_value.return_value = "Bearer RENEWED-SYNTHETIC"
                page.context.events["response"](Mock(url=API_URL, request=request, status=200))
                return
            return original(page, *args, **kwargs)
        with patch.object(TestPage, "wait_for_function", renew):
            result = self.invoke([plan(DRY[-1:], "DryLogin")], portal_statuses=(401,))
        self.assertEqual(result["event_counts"], [2])
        self.assertEqual(self.page.starts, 1)
        self.assertFalse(result["diagnostics"]["session_reauthentication"])
