import json
import unittest
from unittest.mock import Mock

from provadis_m1.errors import BootstrapError
from provadis_m1.navigation import StudentNavigation
from provadis_m1.__main__ import parser

ORIGIN = "https://hvsweb.provadis-hochschule.de/"
TARGET = ORIGIN + "student/schedule"


class BrowserError(Exception):
    pass


class NavigationTests(unittest.TestCase):
    def advance(self, nav, page, now=0):
        nav.advance(page, portal_origin=ORIGIN, target_url=TARGET, now=now,
                    timeout_ms=1000, pw_error=BrowserError)

    def page(self, url=ORIGIN, count=1, visible=True):
        page = Mock(url=url)
        page.get_by_role.return_value.filter.return_value.count.return_value = count if visible else 0
        page.get_by_text.return_value.filter.return_value.count.return_value = 0
        return page

    def test_student_entry_clicked_once_with_exact_label(self):
        nav, page = StudentNavigation(), self.page()
        self.advance(nav, page)
        self.advance(nav, page, 1)
        page.get_by_role.assert_called_once_with(
            "heading", name="Für Studierende", level=2, exact=True
        )
        page.get_by_role.return_value.filter.assert_called_once_with(visible=True)
        page.get_by_text.assert_not_called()
        page.get_by_role.return_value.filter.return_value.click.assert_called_once_with(timeout=1000)
        self.assertTrue(nav.student_entry_clicked)

    def test_never_interacts_with_identity_or_other_origins_or_callbacks(self):
        for url in ("https://login.microsoftonline.com/SECRET", ORIGIN + "?code=SECRET",
                    ORIGIN + "#code=SECRET", "https://example.invalid/",
                    "http://hvsweb.provadis-hochschule.de/",
                    "https://hvsweb.provadis-hochschule.de:8443/"):
            nav, page = StudentNavigation(), self.page(url)
            self.advance(nav, page)
            page.get_by_role.assert_not_called()
            page.goto.assert_not_called()
            self.assertNotIn("SECRET", json.dumps(nav.diagnostics()))

    def test_ambiguous_entry_is_not_clicked(self):
        nav, page = StudentNavigation(), self.page(count=2)
        with self.assertRaises(BootstrapError):
            self.advance(nav, page)
        page.get_by_role.return_value.filter.return_value.click.assert_not_called()

    def test_missing_or_hidden_entry_does_not_trigger_a_guess(self):
        for page in (self.page(count=0), self.page(visible=False)):
            self.advance(StudentNavigation(), page)
            page.get_by_role.return_value.filter.return_value.click.assert_not_called()

    def test_student_page_never_uses_full_document_schedule_fallback(self):
        nav, page = StudentNavigation(), self.page(ORIGIN + "student/overview")
        self.advance(nav, page, 0)
        self.advance(nav, page, 1)
        page.goto.assert_not_called()
        self.advance(nav, page, 2)
        self.advance(nav, page, 3)
        page.goto.assert_not_called()

    def test_no_navigation_when_schedule_already_open(self):
        nav, page = StudentNavigation(), self.page(TARGET)
        self.advance(nav, page, 0)
        self.advance(nav, page, 3)
        page.goto.assert_not_called()
        page.get_by_text.assert_not_called()

    def test_click_failure_is_safe_and_not_retried(self):
        nav, page = StudentNavigation(), self.page()
        page.get_by_role.return_value.filter.return_value.click.side_effect = BrowserError("SECRET")
        with self.assertRaises(BootstrapError) as raised:
            self.advance(nav, page)
        self.assertEqual(str(raised.exception), "student_entry_click_failed")
        self.advance(nav, page, 2)
        page.get_by_role.return_value.filter.return_value.click.assert_called_once()

    def test_cli_defaults_to_confirmed_schedule_path(self):
        self.assertEqual(parser().parse_args(["fetch"]).portal_path, "/student/schedule")

    def test_jetzt_anmelden_clicked_once_only_on_student_login_page(self):
        nav, page = StudentNavigation(), self.page(ORIGIN + 'login/student')
        self.advance(nav, page)
        self.advance(nav, page, 1)
        page.get_by_role.assert_called_once_with('button', name='Jetzt anmelden', exact=True)
        page.get_by_role.return_value.filter.return_value.click.assert_called_once()
        self.assertTrue(nav.sign_in_clicked)
        page.goto.assert_not_called()

    def test_dedicated_student_sso_selected_once_on_exact_b2c_origin(self):
        nav = StudentNavigation(sign_in_clicked=True)
        page = self.page('https://ishgroupb2c.b2clogin.com/authorize?state=SECRET')
        self.advance(nav, page)
        self.advance(nav, page, 1)
        page.get_by_role.assert_called_once_with(
            'button', name='Anmeldung für Studierende und Lehrende', exact=True)
        self.assertTrue(nav.student_sso_clicked)
        self.assertNotIn('SECRET', json.dumps(nav.diagnostics()))
        page.get_by_text.assert_not_called()

    def test_b2c_form_not_touched_without_portal_signin_step(self):
        page = self.page('https://ishgroupb2c.b2clogin.com/?state=SECRET')
        self.advance(StudentNavigation(), page)
        page.get_by_role.assert_not_called()

    def test_no_microsoft_credential_or_confirmation_interactions(self):
        for url in ('https://login.microsoftonline.com/common/oauth2/authorize',
                    'https://login.live.com/login.srf',
                    'https://ishgroupb2c.b2clogin.com.evil.invalid/'):
            nav = StudentNavigation(sign_in_clicked=True, student_sso_clicked=True)
            page = self.page(url)
            self.advance(nav, page)
            page.get_by_role.assert_not_called()
            page.get_by_text.assert_not_called()
            page.fill.assert_not_called()
            page.goto.assert_not_called()

    def test_oauth_portal_callback_not_interrupted_after_sso_selection(self):
        for url in (ORIGIN + '?code=SECRET', ORIGIN + '#code=SECRET'):
            page = self.page(url)
            nav = StudentNavigation(sign_in_clicked=True, student_sso_clicked=True)
            self.advance(nav, page)
            page.get_by_text.assert_not_called()
            page.goto.assert_not_called()

    def test_return_to_portal_root_reenters_student_card_once_without_reload(self):
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True)
        page = self.page()
        self.advance(nav, page, 0)
        self.advance(nav, page, 2)
        self.advance(nav, page, 5)
        page.goto.assert_not_called()
        page.get_by_role.assert_called_once_with(
            'heading', name='Für Studierende', level=2, exact=True)
        page.get_by_role.return_value.filter.return_value.click.assert_called_once()
        self.assertTrue(nav.post_sso_student_entry_clicked)

    def test_reported_menu_order_and_no_repeated_clicks(self):
        page = self.page(ORIGIN + 'student/overview')
        menu, schedule = Mock(), Mock()
        menu.filter.return_value.count.return_value = 1
        schedule.filter.return_value.count.return_value = 1
        page.get_by_text.side_effect = lambda label, **kw: {
            'My Studium': menu, 'Stundenplan': schedule}[label]
        nav = StudentNavigation()
        self.advance(nav, page, 0)
        schedule.filter.return_value.click.assert_not_called()
        self.advance(nav, page, 1)
        self.advance(nav, page, 2)
        menu.filter.return_value.click.assert_called_once()
        schedule.filter.return_value.click.assert_called_once()
        self.assertTrue(nav.my_studium_clicked)
        self.assertTrue(nav.timetable_link_clicked)

    def test_post_sso_reentry_waits_for_callback_to_clear(self):
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True)
        page = self.page(ORIGIN + '#code=SECRET')
        self.advance(nav, page)
        page.get_by_role.assert_not_called()
        page.url = ORIGIN
        self.advance(nav, page, 1)
        self.assertTrue(nav.post_sso_student_entry_clicked)
        page.goto.assert_not_called()

    def test_visible_student_menu_is_used_before_post_sso_card(self):
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True)
        page = self.page()
        page.get_by_text.return_value.filter.return_value.count.return_value = 1
        self.advance(nav, page)
        self.assertTrue(nav.my_studium_clicked)
        self.assertFalse(nav.post_sso_student_entry_clicked)
        page.get_by_role.assert_not_called()
        page.goto.assert_not_called()

    def test_reentry_does_not_restart_signin_loop(self):
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True)
        page = self.page()
        self.advance(nav, page)
        page.url = ORIGIN + 'login/student'
        page.get_by_role.reset_mock()
        for tick in range(10):
            self.advance(nav, page, tick)
        page.get_by_role.assert_not_called()
        page.goto.assert_not_called()

    def test_missing_selector_logged_once_per_action_without_guessing(self):
        events = []
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True,
                                trace=lambda event, **kw: events.append((event, kw)))
        page = self.page(count=0)
        for tick in range(10):
            self.advance(nav, page, tick)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e[0] == 'selector_missing' and e[1]['matches'] == 0 for e in events))
        self.assertFalse(nav.post_sso_student_entry_clicked)
        self.assertFalse(nav.schedule_navigation_attempted)
        page.goto.assert_not_called()

    def test_reentry_is_ambiguous_when_multiple_visible_cards_remain(self):
        nav = StudentNavigation(student_entry_clicked=True, sign_in_clicked=True,
                                student_sso_clicked=True)
        page = self.page(count=2)
        with self.assertRaises(BootstrapError) as raised:
            self.advance(nav, page)
        self.assertEqual(str(raised.exception), 'ambiguous_post_sso_student_entry')
        page.get_by_role.return_value.filter.return_value.click.assert_not_called()
