"""Deterministic detached-handle/request regressions; no browser or account.

The adapter models dispatch evidence, not Microsoft's JavaScript. The optional
Chromium tests exercise the actual capture listener and element detachment.
"""

import json
import unittest
from unittest.mock import Mock, patch

from auth_support import Locator, Node, Page
from provadis_m1.auth_flow import AuthFlow, MICROSOFT
from provadis_m1.errors import AuthenticationError, BootstrapError
from test_auth_flow import CREDS


class CredentialStepTests(unittest.TestCase):
    def setUp(self):
        self.page = Page("DryLogin-7_SignIn-EnterPassword.html", MICROSOFT + "/")
        self.flow = AuthFlow(credentials=CREDS, state_seconds=2)
        self.posts = []
        self.page.on_credential_request = self.post

    def post(self, action):
        request = Mock(url=action, method="POST", frame=self.page.main_frame)
        self.posts.append(request)
        self.flow.observe_request(request)

    def advance(self, now=1):
        return self.flow.advance(self.page, now=now, timeout_ms=1000)

    def replace(self, locator):
        node = locator.nodes[0]
        replacement = Node(node.tag, node.attrs.items())
        replacement.form = node.form
        index = self.page.document.nodes.index(node)
        self.page.document.nodes[index] = replacement

    def test_replaced_submit_is_reacquired_with_one_attempt_and_one_post(self):
        original = Locator.click
        replacements = []

        def click(locator, **kwargs):
            if not replacements:
                self.replace(locator)
                replacements.append(1)
            return original(locator, **kwargs)

        with patch.object(Locator, "click", click):
            self.advance()
        self.assertEqual(len(self.page.clicks), 1)
        self.assertEqual(len(self.posts), 1)
        diag = self.flow.diagnostics()
        self.assertEqual(diag["auth_actions"]["password"], 1)
        self.assertEqual(diag["credential_control_recoveries"], {"password": 1})
        self.assertEqual(diag["credential_submissions_observed"], {"password": 1})
        self.assertEqual(diag["credential_submission_outcomes"], {"password": "request_observed"})
        self.assertNotIn(CREDS.password, json.dumps(diag))
        self.assertNotIn("common/login", json.dumps(diag))

    def test_dispatched_click_then_detach_never_clicks_twice(self):
        del self.page.on_credential_request  # No request has been observed yet.
        original = Locator.click

        def click(locator, **kwargs):
            original(locator, **kwargs)
            self.replace(locator)
            raise RuntimeError("Element is not attached to the DOM")

        with patch.object(Locator, "click", click):
            self.advance()
            self.assertFalse(self.advance(1.5))
        self.assertEqual(len(self.page.clicks), 1)
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 0})
        self.assertEqual(self.flow.diagnostics()["credential_submission_outcomes"], {"password": "uncertain"})
        # A later matching request updates the evidence without another click.
        self.post(MICROSOFT + "/common/login")
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 1})
        with self.assertRaisesRegex(BootstrapError, "authentication_state_timeout_password"):
            self.advance(4)
        self.assertEqual(len(self.page.clicks), 1)

    def test_document_evidence_loss_during_action_is_not_retried(self):
        def click(locator, **kwargs):
            self.page.load("DryLogin-7_SignIn-EnterPassword.html")
            raise RuntimeError("Element is not attached to the DOM")

        with patch.object(Locator, "click", click):
            self.advance()
            self.assertFalse(self.advance(1.5))
        self.assertEqual(self.page.clicks, [])
        self.assertEqual(self.flow.diagnostics()["credential_submission_outcomes"], {"password": "uncertain"})

    def test_retry_checks_origin_before_refilling(self):
        original = Locator.fill
        fills = []

        def fill(locator, value, **kwargs):
            fills.append(1)
            return original(locator, value, **kwargs)

        def click(locator, **kwargs):
            self.page.url = "https://untrusted.invalid/"
            raise RuntimeError("Element is not attached to the DOM")

        with patch.object(Locator, "fill", fill), patch.object(Locator, "click", click):
            self.advance()
            with self.assertRaisesRegex(AuthenticationError, "unexpected_authentication_origin"):
                self.advance(1.5)
        self.assertEqual(len(fills), 1)
        self.assertEqual(self.page.clicks, [])

    def test_retry_cannot_fill_a_replacement_form_with_untrusted_action(self):
        def click(locator, **kwargs):
            self.replace(locator)
            locator.nodes[0].form.attrs["action"] = "https://untrusted.invalid/"
            raise RuntimeError("Element is not attached to the DOM")

        with patch.object(Locator, "click", click):
            with self.assertRaisesRegex(BootstrapError, "credential_control_recovery_exhausted"):
                self.advance()
        self.assertEqual(self.page.clicks, [])
        self.assertEqual(self.posts, [])
        self.assertEqual(self.flow.actions["password"], 1)

    def test_recovery_budget_is_finite_and_preserves_credential_limit(self):
        def click(locator, **kwargs):
            self.replace(locator)
            return Locator.click_before_replace(locator, **kwargs)

        # Save the real adapter click so each stale handle actually fails.
        with patch.object(Locator, "click_before_replace", Locator.click, create=True), \
                patch.object(Locator, "click", click):
            with self.assertRaisesRegex(BootstrapError, "credential_control_recovery_exhausted"):
                self.advance()
        self.assertEqual(self.flow.actions["password"], 1)
        self.assertEqual(self.posts, [])
        self.flow.after_expiry()
        with self.assertRaisesRegex(AuthenticationError, "credential_form_repeated_password"):
            self.advance()

    def test_recovery_does_not_reset_deadline(self):
        clock = [0.0]
        budgets = []
        original = Locator.fill

        def fill(locator, value, **kwargs):
            budgets.append(kwargs["timeout"])
            return original(locator, value, **kwargs)

        def click(locator, **kwargs):
            clock[0] += .6
            self.replace(locator)
            raise RuntimeError("Element is not attached to the DOM")

        with patch("provadis_m1.credential_step.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(Locator, "fill", fill), patch.object(Locator, "click", click):
            with self.assertRaisesRegex(BootstrapError, "credential_control_recovery_timeout"):
                self.advance()
        self.assertEqual(len(budgets), 2)
        self.assertLess(budgets[1], budgets[0] / 2)
        self.assertEqual(self.flow.actions["password"], 1)

    def test_replaced_field_before_prepare_is_safe_to_reacquire(self):
        original = Locator.element_handle
        changed = []

        def handle(locator, **kwargs):
            result = original(locator, **kwargs)
            if locator.nodes[0].attrs.get("name") == "passwd" and not changed:
                self.replace(locator)
                changed.append(1)
            return result

        with patch.object(Locator, "element_handle", handle):
            self.advance()
        self.assertEqual(len(self.posts), 1)

    def test_request_observation_matches_exact_action_method_and_frame(self):
        del self.page.on_credential_request
        self.advance()
        action = MICROSOFT + "/common/login"
        for method, url, frame in (("GET", action, self.page.main_frame),
                                   ("POST", MICROSOFT + "/kmsi", self.page.main_frame),
                                   ("POST", action + "?different", self.page.main_frame),
                                   ("POST", action, object()),
                                   ("POST", "https://untrusted.invalid/common/login", self.page.main_frame)):
            self.flow.observe_request(Mock(method=method, url=url, frame=frame))
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 0})
        self.post(action)
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 1})

    def test_normal_click_without_request_is_not_observed_submission(self):
        del self.page.on_credential_request
        self.advance()
        self.assertEqual(len(self.page.clicks), 1)
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 0})
        self.assertEqual(self.flow.diagnostics()["credential_submission_outcomes"], {"password": "awaiting_request"})

    def test_later_form_post_cannot_be_attributed_to_previous_phase(self):
        del self.page.on_credential_request
        self.advance()
        self.page.load("DryLogin-8_SignIn-Store.html")
        self.advance(1.1)
        self.post(MICROSOFT + "/common/login")
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 0})

    def test_request_observer_never_reads_credentials_or_headers(self):
        class MetadataOnly:
            method, url, frame = "POST", MICROSOFT + "/common/login", self.page.main_frame

            @property
            def post_data(self):
                raise AssertionError("must not read the body")

            @property
            def headers(self):
                raise AssertionError("must not read the headers")

        del self.page.on_credential_request
        self.advance()
        self.flow.observe_request(MetadataOnly())
        self.assertEqual(self.flow.diagnostics()["credential_submissions_observed"], {"password": 1})


if __name__ == "__main__":
    unittest.main()
