import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from provadis_m1.credentials import LoginCredentials
from provadis_m1.errors import BootstrapError
from provadis_m1.navigation_log import NavigationLog
from provadis_m1.password_login import PasswordLogin

USER = 'synthetic.person@example.invalid'
SECRET = 'SYNTHETIC-password-not-a-credential'
EMAIL = 'input[name="loginfmt"]'
PASSWORD = 'input[name="passwd"][type="password"]'
BUTTON = '#idSIButton9'
KMSI = '#KmsiDescription'


class BrowserError(Exception):
    pass


class PasswordLoginTests(unittest.TestCase):
    def setUp(self):
        self.controls = {}
        self.page = Mock(url='https://login.microsoftonline.com/common/oauth2/authorize')
        self.page.locator.side_effect = self.locator
        self.login = PasswordLogin(LoginCredentials(USER, SECRET))

    def locator(self, selector):
        if selector not in self.controls:
            self.controls[selector] = Mock()
            self.controls[selector].filter.return_value.count.return_value = 0
            visible = self.controls[selector].filter.return_value
            visible.element_handle.return_value = visible
            visible.evaluate.return_value = True
        return self.controls[selector]

    def show(self, *selectors):
        for control in self.controls.values():
            control.filter.return_value.count.return_value = 0
        for selector in selectors:
            self.locator(selector).filter.return_value.count.return_value = 1

    def advance(self):
        self.login.advance(self.page, timeout_ms=1000, pw_error=BrowserError)

    def test_email_password_and_specific_stay_signed_in_are_submitted_once(self):
        self.show(EMAIL, BUTTON)
        self.advance()
        self.advance()
        self.locator(EMAIL).filter.return_value.fill.assert_called_once_with(USER, timeout=1000)
        self.show(PASSWORD, BUTTON)
        self.advance()
        self.advance()
        self.locator(PASSWORD).filter.return_value.fill.assert_called_once_with(SECRET, timeout=1000)
        self.show(KMSI, BUTTON)
        self.advance()
        self.advance()
        self.assertEqual(self.locator(BUTTON).filter.return_value.click.call_count, 3)
        self.assertTrue(all(self.login.diagnostics().values()))

    def test_other_origins_and_lookalikes_never_receive_credentials(self):
        for url in ('https://hvsweb.provadis-hochschule.de/',
                    'https://ishgroupb2c.b2clogin.com/',
                    'https://login.live.com/',
                    'https://login.microsoft.com/common/bridge/fido',
                    'https://login.microsoftonline.com.evil.invalid/',
                    'https://login.microsoftonline.com:8443/',
                    'https://user@login.microsoftonline.com/',
                    'http://login.microsoftonline.com/'):
            self.page.url = url
            self.advance()
        self.page.locator.assert_not_called()

    def test_password_only_account_picker_mfa_and_generic_yes_are_not_guessed(self):
        self.show(PASSWORD, BUTTON)
        self.advance()
        self.locator(PASSWORD).filter.return_value.fill.assert_not_called()
        self.show(BUTTON)  # May be consent/MFA/account selection: never generic Yes.
        self.login.email_submitted = self.login.password_submitted = True
        self.advance()
        self.locator(BUTTON).filter.return_value.click.assert_not_called()

    def test_duplicate_controls_and_multiple_visible_forms_fail_closed(self):
        self.show(EMAIL, BUTTON)
        self.locator(EMAIL).filter.return_value.count.return_value = 2
        with self.assertRaises(BootstrapError):
            self.advance()
        self.locator(EMAIL).filter.return_value.fill.assert_not_called()

    def test_combined_controls_submit_email_then_password_once(self):
        self.show(EMAIL, PASSWORD, BUTTON)
        self.advance()
        self.locator(EMAIL).filter.return_value.fill.assert_called_once()
        self.locator(PASSWORD).filter.return_value.fill.assert_not_called()
        self.advance()
        self.locator(PASSWORD).filter.return_value.fill.assert_not_called()
        self.show(PASSWORD, BUTTON)
        self.advance()
        self.advance()
        self.locator(PASSWORD).filter.return_value.fill.assert_called_once()
        self.assertEqual(self.locator(BUTTON).filter.return_value.click.call_count, 2)

    def test_browser_error_cannot_leak_password_or_trigger_a_second_attempt(self):
        self.login.email_submitted = True
        self.show(PASSWORD, BUTTON)
        self.locator(BUTTON).filter.return_value.click.side_effect = BrowserError(SECRET)
        with self.assertRaises(BootstrapError) as raised:
            self.advance()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertTrue(self.login.diagnostics()['credential_password_attempted'])
        self.assertFalse(self.login.password_submitted)
        self.advance()
        self.locator(PASSWORD).filter.return_value.fill.assert_called_once()
        self.locator(BUTTON).filter.return_value.click.assert_called_once()

    def test_opt_in_log_and_diagnostics_never_contain_credential_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with NavigationLog(Path(directory)) as trace:
                self.login.trace = trace
                self.show(EMAIL, BUTTON)
                self.advance()
                self.show(PASSWORD, BUTTON)
                self.advance()
            serialized = trace.path.read_text() + json.dumps(self.login.diagnostics()) + repr(self.login)
            self.assertIn('credential_step_completed', serialized)
            self.assertNotIn(USER, serialized)
            self.assertNotIn(SECRET, serialized)

    def test_origin_change_after_locator_lookup_never_receives_credentials(self):
        self.show(EMAIL, BUTTON)
        email = self.locator(EMAIL).filter.return_value
        email.evaluate.return_value = False
        with self.assertRaisesRegex(BootstrapError, 'microsoft_login_origin_changed'):
            self.advance()
        email.fill.assert_not_called()
        email.dispose.assert_called_once()
        self.locator(BUTTON).filter.return_value.click.assert_not_called()
