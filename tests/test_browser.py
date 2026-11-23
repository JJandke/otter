import unittest
import json
import types
from pathlib import Path
from unittest.mock import Mock, patch

from provadis_m1.browser import (
    API_URL, PORTAL_URL, PortalCapture, browser_run, direct_fetch, portal_url,
)
from provadis_m1.errors import (
    AuthenticationError, BootstrapError, ConfigurationError, InvalidResponse, NetworkError,
    ServiceError,
)

VALID = (Path(__file__).parent / "fixtures" / "valid.json").read_bytes()


class SyntheticPlaywrightError(Exception):
    pass


class BrowserBoundaryTests(unittest.TestCase):
    def test_missing_request_is_unknown_bootstrap_not_confirmed_auth_failure(self):
        with self.assertRaises(BootstrapError) as raised:
            PortalCapture().require_bearer()
        self.assertEqual(raised.exception.code, 8)

    def test_diagnostics_never_export_url_header_or_error_text(self):
        capture = PortalCapture(bearer="Bearer SECRET")
        capture.observe_page_error(RuntimeError("SECRET"))
        for url, expected in (
            (PORTAL_URL + "?code=SECRET#token=SECRET", "portal"),
            ("https://ishgroupb2c.b2clogin.com/path?code=SECRET", "identity_provider"),
            ("https://example.invalid/SECRET", "other"),
            ("about:blank", "blank"),
        ):
            result = capture.diagnostics(url, headed=False)
            self.assertEqual(result["final_location"], expected)
            self.assertEqual(result["page_error_count"], 1)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_bootstrap_failure_retains_diagnostics_and_closes_browser(self):
        page = Mock(url="https://ishgroupb2c.b2clogin.com/?code=SECRET")
        context = Mock(pages=[page])
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = context
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        module = types.ModuleType("playwright.sync_api")
        module.Error = SyntheticPlaywrightError
        module.sync_playwright = Mock(return_value=manager)
        with patch.dict("sys.modules", {"playwright.sync_api": module}):
            with self.assertRaises(BootstrapError) as raised:
                browser_run(Path("unused-synthetic-profile"), interactive=False,
                            bootstrap_seconds=0, request_seconds=1, samples=1,
                            sample_interval=0, headed=True)
        self.assertEqual(raised.exception.diagnostics["final_location"], "identity_provider")
        self.assertEqual(raised.exception.diagnostics["browser_mode"], "headed")
        self.assertNotIn("SECRET", json.dumps(raised.exception.diagnostics))
        context.close.assert_called_once()
        page.goto.assert_called_once_with(
            PORTAL_URL, wait_until="domcontentloaded", timeout=1000
        )
        self.assertFalse(
            playwright.chromium.launch_persistent_context.call_args.kwargs["headless"]
        )

    def request(self, url=API_URL, method="GET", bearer="Bearer SYNTHETIC-NOT-A-CREDENTIAL"):
        request = Mock(url=url, method=method)
        request.header_value.return_value = bearer
        return request

    def test_capture_exact_api_only(self):
        capture = PortalCapture()
        for request in (
            self.request("https://example.invalid/"),
            self.request(API_URL + "?something=1"),
            self.request(API_URL, "POST"),
            self.request(API_URL + "/"),
        ):
            capture.observe(request)
            capture.observe_response(Mock(url=request.url, request=request, status=200))
            request.header_value.assert_not_called()
        self.assertIsNone(capture.bearer)
        request = self.request()
        capture.observe(request)
        capture.observe_response(Mock(url=API_URL, request=request, status=200))
        self.assertEqual(capture.bearer, "Bearer SYNTHETIC-NOT-A-CREDENTIAL")
        self.assertNotIn("SYNTHETIC-NOT-A-CREDENTIAL", repr(capture))

    def test_invalid_headers_never_captured(self):
        for value in (None, "", "Basic example", "Bearer ", "Bearer token\nheader"):
            capture = PortalCapture()
            request = self.request(bearer=value)
            capture.observe_response(Mock(url=API_URL, request=request, status=200))
            self.assertIsNone(capture.bearer)

    def test_stale_request_does_not_preempt_portal_renewal(self):
        capture = PortalCapture()
        stale = self.request(bearer="Bearer STALE-SYNTHETIC")
        capture.observe(stale)
        capture.observe_response(Mock(url=API_URL, request=stale, status=401))
        self.assertIsNone(capture.bearer)
        stale.header_value.assert_not_called()
        fresh = self.request(bearer="Bearer FRESH-SYNTHETIC")
        capture.observe_response(Mock(url=API_URL, request=fresh, status=200))
        self.assertEqual(capture.require_bearer(), "Bearer FRESH-SYNTHETIC")

    def test_bootstrap_api_transport_and_service_errors_are_distinct(self):
        capture = PortalCapture()
        capture.observe_failed(self.request())
        with self.assertRaises(NetworkError):
            capture.require_bearer()
        capture = PortalCapture()
        capture.observe_response(Mock(url=API_URL, request=self.request(), status=503))
        with self.assertRaises(ServiceError):
            capture.require_bearer()

    def response(self):
        return Mock(
            url=API_URL, status=200,
            headers={"content-type": "application/json"},
            body=Mock(return_value=VALID),
        )

    def test_direct_get_is_fixed_and_redirects_are_disabled(self):
        context = Mock()
        response = self.response()
        context.request.get.return_value = response
        result = direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        self.assertEqual(result.event_count, 2)
        context.request.get.assert_called_once_with(
            API_URL, headers={"Authorization": "Bearer SYNTHETIC", "Accept": "application/json"},
            timeout=1000, max_redirects=0, max_retries=0, fail_on_status_code=False,
        )
        response.dispose.assert_called_once()

    def test_network_details_do_not_escape(self):
        context = Mock()
        context.request.get.side_effect = SyntheticPlaywrightError("Bearer SECRET sentinel")
        with self.assertRaises(NetworkError) as raised:
            direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        self.assertNotIn("SECRET", str(raised.exception))

    def test_body_failure_classified_as_network_and_disposed(self):
        response = self.response()
        response.body.side_effect = SyntheticPlaywrightError("SECRET")
        context = Mock()
        context.request.get.return_value = response
        with self.assertRaises(NetworkError):
            direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        response.dispose.assert_called_once()

    def test_invalid_response_is_disposed(self):
        response = self.response()
        response.body.return_value = b"SECRET upstream body"
        context = Mock()
        context.request.get.return_value = response
        with self.assertRaises(InvalidResponse) as raised:
            direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        self.assertNotIn("SECRET", str(raised.exception))
        response.dispose.assert_called_once()

    def test_known_auth_status_does_not_require_reading_body(self):
        response = self.response()
        response.status = 401
        response.body.side_effect = SyntheticPlaywrightError("SECRET")
        context = Mock()
        context.request.get.return_value = response
        with self.assertRaises(AuthenticationError):
            direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        response.body.assert_not_called()
        response.dispose.assert_called_once()

    def test_portal_route_remains_on_fixed_origin(self):
        self.assertEqual(portal_url("/#/Student/Timetable"), PORTAL_URL + "#/Student/Timetable")
        for value in ("//example.invalid/", "https://example.invalid/", "/?code=SECRET",
                      "/#access_token=SECRET", "/../other"):
            with self.subTest(path=value), self.assertRaises(ConfigurationError):
                portal_url(value)

    def test_oversized_body_rejected_using_length_before_read(self):
        response = self.response()
        response.headers["content-length"] = str(9 * 1024 * 1024)
        context = Mock()
        context.request.get.return_value = response
        with self.assertRaises(InvalidResponse):
            direct_fetch(context, "Bearer SYNTHETIC", 1000, SyntheticPlaywrightError)
        response.body.assert_not_called()
        response.dispose.assert_called_once()
