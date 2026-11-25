"""Opt-in real Chromium, synthetic transitions, sanitized captured controls.

All requests are routed locally. No Microsoft JavaScript, account or live API is
used. Native browser Cancel and tenant-specific redirects need live acceptance.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from auth_support import FIXTURES
from test_auth_flow import CREDS, DRY
from test_auth_runner import WET
from provadis_m1.auth_flow import B2C, MICROSOFT, PORTAL, AuthFlow, controls, wait_for_change
from provadis_m1.browser import API_URL
from provadis_m1.failure_diagnostics import capture_failure
from provadis_m1.passkey import DECLINE_PASSKEY_SCRIPT
from provadis_m1.validation import validate_response

VALID = (Path(__file__).parent / "fixtures" / "valid.json").read_bytes()


@unittest.skipUnless(os.environ.get("OTTER_BROWSER_TESTS") == "1", "set OTTER_BROWSER_TESTS=1")
class ChromiumFixtureTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import Error, TimeoutError, sync_playwright
        self.pw_error, self.pw_timeout = Error, TimeoutError
        self.manager = sync_playwright().start()
        self.addCleanup(self.manager.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def launch(self, profile="profile"):
        context = self.manager.chromium.launch_persistent_context(
            str(Path(self.temp.name) / profile), headless=True, channel="chromium",
            chromium_sandbox=True, accept_downloads=False,
        )
        self.addCleanup(context.close)
        from provadis_m1.credential_step import CREDENTIAL_CAPTURE_SCRIPT
        context.add_init_script(CREDENTIAL_CAPTURE_SCRIPT)
        return context

    def route(self, context, rows, prefix):
        documents = {host + f"/otter-fixture/{i}":
                     (FIXTURES / f"{prefix}-{name}.html").read_text()
                     for i, (name, host, _) in enumerate(rows)}

        def handle(route):
            if route.request.url == API_URL:
                route.fulfill(status=200, content_type="application/json", body=VALID,
                              headers={"Access-Control-Allow-Origin": PORTAL})
            elif route.request.url in documents:
                route.fulfill(status=200, content_type="text/html",
                              body=documents[route.request.url])
            else:
                route.abort()  # Proves the test cannot contact real services.

        context.route("**/*", handle)
        return list(documents)

    def drive(self, context, rows, prefix):
        urls = self.route(context, rows, prefix)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(urls[0])
        flow = AuthFlow(credentials=CREDS)
        for i, (_, _, state) in enumerate(rows):
            self.assertEqual(flow.detect(page)[0], state)
            if i == len(rows) - 1:
                break
            _, locator = flow.detect(page)
            if state in {"kmsi", "email", "password"}:
                locator = controls(page)["submit"]
            # This is synthetic test JS, not a reconstruction of Microsoft's JS.
            locator.evaluate("(node, url) => node.addEventListener('click', event => {event.preventDefault(); location.href = url;})",
                             urls[i + 1])
            self.assertTrue(flow.advance(page, now=time.monotonic(), timeout_ms=3000))
            page.wait_for_url(urls[i + 1], timeout=3000)
        with page.expect_response(API_URL) as response:
            page.evaluate("url => fetch(url)", API_URL)
        result = response.value
        self.assertEqual(validate_response(result.status, result.headers["content-type"],
                                           result.body()).event_count, 2)
        return flow, page

    def test_all_fifteen_captured_fixture_states_use_real_locators(self):
        context = self.launch()
        page = context.pages[0] if context.pages else context.new_page()
        for prefix, rows in (("DryLogin", DRY), ("WetLogin", WET)):
            urls = self.route(context, rows, prefix)
            for url, (_, _, state) in zip(urls, rows, strict=True):
                page.goto(url)
                self.assertEqual(AuthFlow().detect(page)[0], state)
            context.unroute("**/*")

    def test_dry_then_wet_with_actual_persistent_profile_restart(self):
        context = self.launch()
        flow, _ = self.drive(context, DRY, "DryLogin")
        self.assertEqual(flow.actions["password"], 1)
        context.add_cookies([{"name": "OTTER_TEST", "value": "synthetic",
                              "url": PORTAL, "expires": time.time() + 3600}])
        context.close()
        second = self.launch()
        self.assertTrue(any(c["name"] == "OTTER_TEST" for c in second.cookies()))
        wet, _ = self.drive(second, WET, "WetLogin")
        self.assertEqual(wet.actions["email"], 0)

    def credential_page(self):
        """Real DOM/events/POST, with all bytes fulfilled locally."""
        context = self.launch()
        page = context.pages[0] if context.pages else context.new_page()
        action = MICROSOFT + "/common/login"

        def route(r):
            if r.request.method == "POST" and r.request.url == action:
                r.fulfill(status=200, content_type="text/html", body="<title>Submitted</title>")
            elif r.request.url == MICROSOFT + "/otter-fixture/password":
                r.fulfill(content_type="text/html", body=(FIXTURES /
                          "DryLogin-7_SignIn-EnterPassword.html").read_text())
            else:
                r.abort()

        context.route("**/*", route)
        flow = AuthFlow(credentials=CREDS)
        context.on("request", flow.observe_request)
        page.goto(MICROSOFT + "/otter-fixture/password")
        return page, flow

    def test_submit_replaced_during_real_handle_click_is_reacquired(self):
        from playwright.sync_api import ElementHandle
        page, flow = self.credential_page()
        click = ElementHandle.click
        replaced = []

        def replace_then_click(handle, **kwargs):
            if not replaced:
                handle.evaluate("node => node.replaceWith(node.cloneNode(true))")
                replaced.append(True)
            return click(handle, **kwargs)

        with patch.object(ElementHandle, "click", replace_then_click), \
                page.expect_request(lambda r: r.method == "POST" and r.url == MICROSOFT + "/common/login"):
            self.assertTrue(flow.advance(page, now=time.monotonic(), timeout_ms=3000))
        diag = flow.diagnostics()
        self.assertEqual(diag["auth_actions"]["password"], 1)
        self.assertEqual(diag["credential_control_recoveries"], {"password": 1})
        self.assertEqual(diag["credential_submissions_observed"], {"password": 1})

    def test_real_capture_sees_dispatch_before_site_handler_removes_button(self):
        from playwright.sync_api import ElementHandle
        page, flow = self.credential_page()
        # Cancel native submission, then detach synchronously during dispatch.
        page.locator("#idSIButton9").evaluate("""node => node.addEventListener('click', event => {
          event.preventDefault(); node.replaceWith(node.cloneNode(true));
        })""")
        click = ElementHandle.click

        def dispatched_then_failed(handle, **kwargs):
            click(handle, **kwargs)
            raise self.pw_error("Element is not attached to the DOM")

        with patch.object(ElementHandle, "click", dispatched_then_failed):
            flow.advance(page, now=time.monotonic(), timeout_ms=3000)
            self.assertFalse(flow.advance(page, now=time.monotonic(), timeout_ms=3000))
        diag = flow.diagnostics()
        self.assertEqual(diag["credential_control_recoveries"], {"password": 0})
        self.assertEqual(diag["credential_submissions_observed"], {"password": 0})
        self.assertEqual(diag["credential_submission_outcomes"], {"password": "uncertain"})

    def test_passkey_decline_rejects_only_public_key_requests(self):
        context = self.launch()
        context.route("**/*", lambda r: r.fulfill(body="<!doctype html><title>Test</title>",
                                               content_type="text/html"))
        context.add_init_script(
            "navigator.credentials.get = () => Promise.resolve('preserved');\n"
            + DECLINE_PASSKEY_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(MICROSOFT)
        self.assertEqual(page.evaluate(
            "() => navigator.credentials.get({publicKey:{}}).catch(e => e.name)"),
            "NotAllowedError")
        self.assertEqual(page.evaluate("() => navigator.credentials.get({password:true})"),
                         "preserved")
        page.goto("https://example.invalid/")
        self.assertEqual(page.evaluate("() => navigator.credentials.get({publicKey:{}})"),
                         "preserved")

    def test_dom_wait_wakes_on_a_real_mutation(self):
        context = self.launch()
        page = context.pages[0] if context.pages else context.new_page()
        page.set_content("<p>Fixture</p>")
        page.evaluate("() => setTimeout(() => document.body.dataset.test = 'changed', 100)")
        wait_for_change(page, 3000, self.pw_timeout, self.pw_error)
        self.assertEqual(page.locator("body").get_attribute("data-test"), "changed")

    def test_returned_login_button_is_visible_but_is_not_clicked_again(self):
        context = self.launch()
        documents = {
            PORTAL + "/login/student": "WetLogin-2_Hochschulportal.html",
            B2C + "/provider": "DryLogin-3_Anmeldung.html",
            PORTAL + "/": "WetLogin-3_Hochschulportal.html",
        }

        def route(request):
            fixture = documents.get(request.request.url)
            if fixture is None:
                request.abort()
            else:
                request.fulfill(content_type="text/html", body=(FIXTURES / fixture).read_text())

        context.route("**/*", route)
        page = context.pages[0] if context.pages else context.new_page()
        flow = AuthFlow()
        # Navigation here is explicitly synthetic, with real Chromium controls.
        page.goto(PORTAL + "/login/student")
        self.assertTrue(flow.advance(page, now=1, timeout_ms=3000))
        page.goto(B2C + "/provider")
        self.assertTrue(flow.advance(page, now=2, timeout_ms=3000))
        page.goto(PORTAL + "/login/student")
        button = controls(page)["portal_login"]
        self.assertTrue(button.is_visible())
        button.evaluate("n => n.addEventListener('click', () => document.body.dataset.replayed = 'yes')")
        self.assertFalse(flow.advance(page, now=3, timeout_ms=3000))
        self.assertIsNone(page.locator("body").get_attribute("data-replayed"))
        page.set_content("<!doctype html><body></body>")
        self.assertFalse(flow.advance(page, now=4, timeout_ms=3000))
        page.goto(PORTAL + "/")
        self.assertTrue(flow.advance(page, now=5, timeout_ms=3000))
        self.assertEqual(flow.actions["portal_login"], 1)
        self.assertEqual(flow.state, "study")

    def test_failure_projection_screenshots_only_sanitized_controls(self):
        context = self.launch()
        _, page = self.drive(context, DRY, "DryLogin")
        # Secret canary would be exposed by a raw page screenshot/content dump.
        page.evaluate("() => document.body.append('SECRET-CANARY')")
        artifacts = capture_failure(page, Path(self.temp.name), {"auth_state": "timetable"},
                                    self.pw_error)
        self.assertIn("control_layout", artifacts)
        report = Path(self.temp.name) / artifacts["report"]
        self.assertNotIn("SECRET-CANARY", report.read_text())
        self.assertEqual(json.loads(report.read_text())["controls"]["timetable"], 1)
