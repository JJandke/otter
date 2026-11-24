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

from auth_support import FIXTURES
from test_auth_flow import CREDS, DRY
from test_auth_runner import WET
from provadis_m1.auth_flow import MICROSOFT, PORTAL, AuthFlow, controls, wait_for_change
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
            locator.evaluate("(node, url) => node.addEventListener('click', () => location.href = url)",
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
