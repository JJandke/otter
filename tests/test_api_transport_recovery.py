"""Offline transport/request metadata tests; no account or network."""
import json
import unittest
from unittest.mock import Mock, patch

from provadis_m1.acceptance import project_summary
from provadis_m1.browser import PortalCapture, direct_fetch
from provadis_m1.errors import AuthenticationError, NetworkError, ServiceError
import test_browser
from test_browser import SyntheticPlaywrightError


class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.response = test_browser.BrowserBoundaryTests().response()
        self.capture = PortalCapture()
        self.trace = Mock()

    def fetch(self):
        return direct_fetch(self.context, "Bearer SYNTHETIC", 1000,
                            SyntheticPlaywrightError, self.capture, self.trace)

    def test_transient_reset_then_success_validates_data_without_login(self):
        self.context.request.get.side_effect = [SyntheticPlaywrightError("ECONNRESET SECRET"), self.response]
        self.assertEqual(self.fetch().event_count, 2)
        self.assertEqual(self.capture.direct_attempts, 2)
        self.assertEqual(self.capture.direct_transport_failures, 1)
        self.assertEqual(self.capture.direct_status, 200)
        self.assertNotIn("SECRET", str(self.trace.call_args_list))
        self.response.dispose.assert_called_once()
        for call in self.context.request.get.call_args_list:
            self.assertEqual(call.kwargs["max_retries"], 0)
            self.assertEqual(call.kwargs["max_redirects"], 0)

    def test_timeout_recovery_and_finite_exhaustion(self):
        self.context.request.get.side_effect = SyntheticPlaywrightError("Timeout 1000ms SECRET")
        with self.assertRaisesRegex(NetworkError, "direct_api_timeout"):
            self.fetch()
        self.assertEqual(self.context.request.get.call_count, 3)
        self.assertEqual(self.trace.call_count, 2)
        self.assertEqual(self.capture.direct_transport_failures, 3)

    def test_shared_deadline_is_not_reset(self):
        clock = [0.0]
        def get(*args, **kwargs):
            clock[0] += 2.5
            raise SyntheticPlaywrightError("ECONNRESET")
        self.context.request.get.side_effect = get
        with patch("provadis_m1.browser.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(NetworkError):
                self.fetch()
        self.assertEqual(self.context.request.get.call_count, 2)
        self.assertEqual(self.context.request.get.call_args_list[1].kwargs["timeout"], 500)

    def test_tls_or_closed_context_errors_are_not_retried(self):
        for message in ("certificate verify failed SECRET", "Target page, context or browser has been closed"):
            self.context.request.get.reset_mock()
            self.context.request.get.side_effect = SyntheticPlaywrightError(message)
            with self.assertRaisesRegex(NetworkError, "direct_api_transport_failed"):
                self.fetch()
            self.assertEqual(self.context.request.get.call_count, 1)

    def test_http_auth_and_service_statuses_are_not_transport_retries(self):
        for status, error in ((401, AuthenticationError), (403, AuthenticationError), (503, ServiceError)):
            self.context.request.get.reset_mock()
            self.response.status = status
            self.context.request.get.return_value = self.response
            with self.assertRaises(error):
                self.fetch()
            self.assertEqual(self.context.request.get.call_count, 1)

    def test_failure_reason_survives_acceptance_projection(self):
        for reason in ("direct_api_timeout", "direct_api_transport_failed", "response_read_failed"):
            row = project_summary({"ok": False, "command": "fetch", "category": "network",
                                   "reason": reason, "diagnostics": {"auth_actions": {},
                                   "credential_submissions_observed": {},
                                   "credential_submission_outcomes": {}, "direct_api_attempts": 2,
                                   "direct_api_transport_failures": 1}}, "fetch", 3)
            self.assertEqual(row["reason"], reason)
            self.assertEqual(row["direct_api_attempts"], 2)
            self.assertEqual(row["direct_api_transport_failures"], 1)
            self.assertFalse(row["credential_attempted"])
            self.assertNotIn("Bearer", json.dumps(row))
