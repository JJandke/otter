"""One explicit Microsoft password attempt; no MFA/passkey/consent automation."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .credentials import LoginCredentials
from .errors import BootstrapError


@dataclass
class PasswordLogin:
    credentials: LoginCredentials = field(repr=False)
    trace: Any = field(default=None, repr=False)
    email_submitted: bool = False
    password_submitted: bool = False
    keep_signed_in_clicked: bool = False
    _email_attempted: bool = field(default=False, repr=False)
    _password_attempted: bool = field(default=False, repr=False)
    _keep_attempted: bool = field(default=False, repr=False)

    def _unique(self, page: Any, selector: str) -> Any:
        locator = page.locator(selector).filter(visible=True)
        count = locator.count()
        if count > 1:
            raise BootstrapError('ambiguous_microsoft_login_control')
        return locator if count == 1 else None

    def _act(self, locator: Any, timeout_ms: int, value: str | None = None) -> None:
        # Bind to one DOM element, then verify its document before sending data.
        # A locator alone could re-resolve on a different origin after navigation.
        element = locator.element_handle(timeout=timeout_ms)
        if element is None:
            raise BootstrapError('microsoft_login_control_disappeared')
        try:
            trusted = element.evaluate(
                "node => node.ownerDocument.location.origin === 'https://login.microsoftonline.com'"
            )
            if trusted is not True:
                raise BootstrapError('microsoft_login_origin_changed')
            if value is None:
                element.click(timeout=timeout_ms)
            else:
                element.fill(value, timeout=timeout_ms)
        finally:
            element.dispose()

    def advance(self, page: Any, *, timeout_ms: int, pw_error: type) -> None:
        current = urlsplit(page.url)
        if (current.scheme, current.netloc) != ('https', 'login.microsoftonline.com'):
            return  # Never send credentials to the portal, B2C or another origin.
        try:
            email = self._unique(page, 'input[name="loginfmt"]')
            password = self._unique(page, 'input[name="passwd"][type="password"]')
            # Do not guess if more than one kind of login form is visible.
            if email is not None and password is not None:
                raise BootstrapError('ambiguous_microsoft_login_form')
            if email is not None and not self._email_attempted and not self._password_attempted:
                button = self._unique(page, '#idSIButton9')
                if button is not None:
                    self._email_attempted = True  # Never repeat a partially completed action.
                    self._act(email, timeout_ms, self.credentials.username)
                    self._act(button, timeout_ms)
                    self.email_submitted = True
                    if self.trace:
                        self.trace('credential_step_completed', action='email_submit')
                return
            if password is not None and self.email_submitted and not self._password_attempted:
                button = self._unique(page, '#idSIButton9')
                if button is not None:
                    self._password_attempted = True
                    self._act(password, timeout_ms, self.credentials.password)
                    self._act(button, timeout_ms)
                    self.password_submitted = True
                    if self.trace:
                        self.trace('credential_step_completed', action='password_submit')
                return
            # "Stay signed in" is allowed only with its specific Microsoft marker
            # after our password attempt, never a generic Yes/consent button.
            if (self.password_submitted and not self._keep_attempted
                    and email is None and password is None
                    and self._unique(page, '#KmsiDescription') is not None):
                button = self._unique(page, '#idSIButton9')
                if button is not None:
                    self._keep_attempted = True
                    self._act(button, timeout_ms)
                    self.keep_signed_in_clicked = True
                    if self.trace:
                        self.trace('credential_step_completed', action='keep_signed_in')
        except pw_error:
            raise BootstrapError('microsoft_login_control_failed') from None

    def diagnostics(self) -> dict[str, bool]:
        return {
            'credential_email_attempted': self._email_attempted,
            'credential_password_attempted': self._password_attempted,
            'credential_email_submitted': self.email_submitted,
            'credential_password_submitted': self.password_submitted,
            'keep_signed_in_clicked': self.keep_signed_in_clicked,
        }
