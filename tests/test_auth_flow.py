import json
import unittest

from auth_support import FIXTURES, Document, Page
from provadis_m1.auth_flow import B2C, FIDO, MICROSOFT, PORTAL, AuthFlow
from provadis_m1.credentials import LoginCredentials
from provadis_m1.errors import AuthenticationError, BootstrapError

DRY = [
    ("1_Hochschulportal", PORTAL, "student"),
    ("2_Hochschulportal", PORTAL, "portal_login"),
    ("3_Anmeldung", B2C, "b2c_microsoft"),
    ("4_SignIn-EMail", MICROSOFT, "email"),
    ("6_SignIn-Password", MICROSOFT, "password_choice"),
    ("7_SignIn-EnterPassword", MICROSOFT, "password"),
    ("8_SignIn-Store", MICROSOFT, "kmsi"),
    ("9_Hochschulportal", PORTAL, "study"),
    ("10_Hochschulportal", PORTAL, "schedule"),
    ("11_Hochschulportal", PORTAL, "timetable"),
]
CREDS = LoginCredentials("synthetic@example.invalid", "SYNTHETIC-PASSWORD")


class AuthFlowTests(unittest.TestCase):
    def page(self, short, host=PORTAL, prefix="DryLogin"):
        return Page(f"{prefix}-{short}.html", host + "/")

    def run_step(self, flow, page, now=1):
        return flow.advance(page, now=now, timeout_ms=1000)

    def test_full_dry_sequence_uses_captured_controls(self):
        flow = AuthFlow(credentials=CREDS)
        for i, (name, host, state) in enumerate(DRY):
            page = self.page(name, host)
            self.assertEqual(flow.detect(page)[0], state)
            acted = self.run_step(flow, page, i)
            self.assertEqual(acted, state != "timetable")
            if state == "kmsi":
                self.assertTrue(page.checked)
        self.assertEqual(flow.actions["password"], 1)
        self.assertNotIn(CREDS.password, json.dumps(flow.diagnostics()))

    def test_wet_sequence_does_not_require_b2c_or_email_history(self):
        flow = AuthFlow()
        for i, state in enumerate(("student", "portal_login", "study", "schedule", "timetable"), 1):
            page = self.page(f"{i}_Hochschulportal", prefix="WetLogin")
            self.assertEqual(flow.detect(page)[0], state)
            self.run_step(flow, page, i)
        self.assertNotIn("b2c_microsoft", flow.actions)

    def test_offscreen_loginfmt_does_not_block_password(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        # Reproduce why a naive name+visible selector was wrong.
        self.assertEqual(page.locator('input[name="loginfmt"]').filter(visible=True).count(), 1)
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, page)
        self.assertEqual(page.fills, {"passwd": CREDS.password})

    def test_email_page_offscreen_password_is_not_password_state(self):
        page = self.page("4_SignIn-EMail", MICROSOFT)
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, page)
        self.assertEqual(page.fills, {"loginfmt": CREDS.username})

    def test_study_heading_excludes_duplicate_sidebar_label(self):
        page = self.page("9_Hochschulportal")
        self.assertGreaterEqual(sum(n.text.strip() == "Mein Studium"
                                    for n in page.document.nodes), 2)
        self.run_step(AuthFlow(), page)
        self.assertEqual(page.clicks[0].tag, "h2")

    def test_optional_screens_can_all_be_skipped(self):
        for indexes in ((0, 1, 2, 3, 5, 7, 8, 9), (5, 6, 7, 8, 9), (6, 7, 8, 9), (9,)):
            flow = AuthFlow(credentials=CREDS)
            for i in indexes:
                name, host, _ = DRY[i]
                self.run_step(flow, self.page(name, host), i)
            self.assertEqual(flow.state, "timetable")

    def test_native_passkey_is_a_distinct_manual_state_without_dom_click(self):
        page = Page(None, FIDO + "/common/bridge/fido?cancelUrl=SECRET")
        flow = AuthFlow(manual=True)
        self.assertFalse(self.run_step(flow, page))
        self.assertEqual(flow.state, "native_passkey")
        self.assertEqual(page.clicks, [])
        self.assertNotIn("SECRET", json.dumps(flow.diagnostics()))

    def test_cancel_then_password_choice_is_independent_of_email_flag(self):
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, Page(None, FIDO + "/common/bridge/fido"))
        page = self.page("6_SignIn-Password", MICROSOFT)
        self.run_step(flow, page)
        self.assertEqual(page.clicks[0].attrs["data-test-cred-id"], "1")

    def test_b2c_student_alias_is_available_if_recorded_button_is_missing(self):
        page = self.page("3_Anmeldung", B2C)
        page.document.nodes = [n for n in page.document.nodes
                               if n.text.strip() != "mit meinem Microsoft-Konto anmelden"]
        flow = AuthFlow()
        self.run_step(flow, page)
        self.assertEqual(flow.state, "b2c_student")

    def test_already_authenticated_timetable_performs_no_actions(self):
        flow = AuthFlow()
        page = self.page("11_Hochschulportal")
        self.assertFalse(self.run_step(flow, page))
        self.assertEqual(flow.actions, {})

    def test_expired_session_returns_through_normal_login_controls(self):
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, self.page("11_Hochschulportal"))
        flow.after_expiry()
        for name, host, _ in DRY:
            self.run_step(flow, self.page(name, host))
        self.assertEqual(flow.state, "timetable")

    def test_expired_session_without_credentials_requires_operator(self):
        with self.assertRaisesRegex(AuthenticationError, "session_requires_interactive_login"):
            self.run_step(AuthFlow(), self.page("7_SignIn-EnterPassword", MICROSOFT))

    def test_interactive_credentials_are_left_to_the_operator(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        self.assertFalse(self.run_step(AuthFlow(manual=True), page))
        self.assertEqual(page.fills, {})

    def test_unknown_state_and_changed_elements_time_out(self):
        flow = AuthFlow(state_seconds=2)
        page = Page(None, MICROSOFT + "/")
        self.assertFalse(self.run_step(flow, page, 1))
        with self.assertRaisesRegex(BootstrapError, "authentication_state_timeout_unknown"):
            self.run_step(flow, page, 3)

    def test_missing_submit_does_not_fill_or_click_and_times_out(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        page.document.nodes = [n for n in page.document.nodes if n.attrs.get("id") != "idSIButton9"]
        flow = AuthFlow(credentials=CREDS, state_seconds=2)
        self.assertFalse(self.run_step(flow, page, 1))
        self.assertEqual(page.fills, {})
        with self.assertRaisesRegex(BootstrapError, "timeout_password"):
            self.run_step(flow, page, 4)

    def test_duplicate_controls_fail_closed(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        node = next(n for n in page.document.nodes if n.attrs.get("id") == "i0118")
        page.document.nodes.append(node)
        with self.assertRaisesRegex(BootstrapError, "ambiguous_control_password"):
            self.run_step(AuthFlow(credentials=CREDS), page)
        self.assertFalse(page.fills)

    def test_rejected_password_never_retries(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, page)
        page.document.nodes += Document('<div id="passwordError">SECRET</div>').nodes
        with self.assertRaisesRegex(AuthenticationError, "microsoft_rejected_credentials") as raised:
            self.run_step(flow, page)
        self.assertNotIn("SECRET", str(raised.exception))
        self.assertEqual(flow.actions["password"], 1)

    def test_same_state_is_never_blindly_clicked_again(self):
        flow = AuthFlow()
        page = self.page("1_Hochschulportal")
        for i in range(3):
            self.run_step(flow, page, i)
        self.assertEqual(len(page.clicks), 1)

    def test_bouncing_dashboard_has_bounded_transition_budget(self):
        flow = AuthFlow()
        for _ in range(2):
            self.run_step(flow, self.page("9_Hochschulportal"))
            self.run_step(flow, self.page("10_Hochschulportal"))
            self.run_step(flow, self.page("11_Hochschulportal"))
        with self.assertRaisesRegex(BootstrapError, "transition_limit_study"):
            self.run_step(flow, self.page("9_Hochschulportal"))

    def test_credentials_cannot_be_retried_after_state_changes(self):
        flow = AuthFlow(credentials=CREDS)
        self.run_step(flow, self.page("7_SignIn-EnterPassword", MICROSOFT))
        flow.after_expiry()
        with self.assertRaisesRegex(AuthenticationError, "credential_form_repeated_password"):
            self.run_step(flow, self.page("7_SignIn-EnterPassword", MICROSOFT))

    def test_untrusted_origin_is_rejected_before_any_fill(self):
        for host in ("http://login.microsoftonline.com", "https://login.microsoftonline.com.evil.invalid",
                     "https://login.microsoftonline.com:8443", "https://user:SECRET@login.microsoftonline.com"):
            page = self.page("7_SignIn-EnterPassword", host)
            with self.assertRaisesRegex(AuthenticationError, "unexpected_authentication_origin"):
                self.run_step(AuthFlow(credentials=CREDS), page)
            self.assertFalse(page.fills)

    def test_portal_callback_is_left_to_spa(self):
        page = self.page("9_Hochschulportal")
        page.url += "#code=SECRET"
        flow = AuthFlow()
        self.assertFalse(self.run_step(flow, page))
        self.assertEqual(flow.state, "redirect")

    def test_fixtures_have_no_embedded_scripts_values_or_personal_data(self):
        manifest = json.loads((FIXTURES / "provenance.json").read_text())
        self.assertEqual(len(manifest), 15)
        for row in manifest:
            content = (FIXTURES / row["fixture"]).read_text()
            self.assertNotIn("<script", content)
            self.assertNotIn("jandke", content.lower())
            self.assertNotIn("flowToken", content)
            for n in Document(content).nodes:
                if n.tag == "input" and n.attrs.get("type") != "submit":
                    self.assertNotIn("value", n.attrs)

    def test_origin_change_between_detection_and_action_never_receives_credentials(self):
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        flow = AuthFlow(credentials=CREDS)
        detected = flow.detect(page)
        from unittest.mock import patch
        def redirect(_page):
            _page.url = "https://untrusted.invalid/"
            return detected
        with patch.object(flow, "detect", side_effect=redirect):
            with self.assertRaisesRegex(AuthenticationError, "authentication_control_origin_changed"):
                self.run_step(flow, page)
        self.assertFalse(page.fills)

    def test_changed_form_after_fill_does_not_click_an_unrelated_submit(self):
        from auth_support import Locator
        from unittest.mock import patch
        page = self.page("7_SignIn-EnterPassword", MICROSOFT)
        def stage_change(_locator, _value, **_kwargs):
            page.load("DryLogin-8_SignIn-Store.html")
        with patch.object(Locator, "fill", stage_change):
            with self.assertRaisesRegex(BootstrapError, "authentication_form_changed"):
                self.run_step(AuthFlow(credentials=CREDS), page)
        self.assertEqual(page.clicks, [])
