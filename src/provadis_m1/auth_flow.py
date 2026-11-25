"""Evidence-based state navigation shared by fresh and persistent profiles.

Selectors come from Steps.zip (24 November 2026). Never read credential values,
OAuth parameters, storage, or arbitrary page text for state detection.
"""

import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .credential_step import CredentialStep
from .credentials import LoginCredentials
from .errors import AuthenticationError, BootstrapError

PORTAL = "https://hvsweb.provadis-hochschule.de"
B2C = "https://ishgroupb2c.b2clogin.com"
MICROSOFT = "https://login.microsoftonline.com"
FIDO = "https://login.microsoft.com"
TRUSTED = {PORTAL, B2C, MICROSOFT, FIDO, "https://login.live.com"}

# Off-screen prefill inputs have a bounding box and Playwright considers them
# visible. Exclude Microsoft's captured moveOffScreen class explicitly.
EMAIL = 'input#i0116[name="loginfmt"]:not(.moveOffScreen)'
PASSWORD = 'input#i0118[name="passwd"][type="password"]:not(.moveOffScreen)'

# One entry sequence per bootstrap attempt. The two B2C buttons are aliases.
# A transient return to these controls after SSO is not permission to start SSO
# again. Explicit 401 recovery starts a new attempt; global budgets still apply.
ENTRY_ACTIONS = {
    "student": "student", "portal_login": "portal_login",
    "b2c_microsoft": "provider", "b2c_student": "provider",
}
UI_OPERATIONS = {"detect", "resolve_control", "verify_document", "fill_control",
                 "check_control", "click_control", "wait_for_change"}
UI_ERROR_KINDS = {"timeout", "element_detached", "context_destroyed", "browser_closed", "other"}


def ui_error_kind(error: Exception, timeout_type: type) -> str:
    """Reduce an exception to a fixed category; never retain/emit its raw text."""
    if isinstance(error, timeout_type) or type(error).__name__ == "TimeoutError":
        return "timeout"
    message = str(error).lower()
    if any(marker in message for marker in (
        "execution context was destroyed", "cannot find context with specified id",
    )):
        return "context_destroyed"
    if "not attached to the dom" in message or "element is detached" in message:
        return "element_detached"
    if "target page, context or browser has been closed" in message:
        return "browser_closed"
    return "other"


def origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def controls(page: Any) -> dict[str, Any]:
    """Public, captured controls only; also reused for safe failure diagrams."""
    return {
        "student": page.get_by_role("heading", name="Für Studierende", level=2, exact=True),
        "portal_login": page.get_by_role("button", name="Jetzt anmelden", exact=True),
        "b2c_microsoft": page.get_by_role(
            "button", name="mit meinem Microsoft-Konto anmelden", exact=True
        ),
        "b2c_student": page.get_by_role(
            "button", name="Anmeldung für Studierende und Lehrende", exact=True
        ),
        "email": page.locator(EMAIL),
        "password": page.locator(PASSWORD),
        "password_choice": page.get_by_role("button", name="Use my password", exact=True),
        "submit": page.locator("#idSIButton9"),
        "kmsi": page.locator("#KmsiDescription"),
        "dont_show_again": page.locator("#KmsiCheckboxField"),
        # The heading uniquely selects the dashboard card, excluding its sidebar twin.
        "study": page.get_by_role("heading", name="Mein Studium", level=2, exact=True),
        "schedule": page.get_by_role("heading", name="Stundenplan", level=2, exact=True),
        "timetable": page.locator(".p-panel-title").filter(has_text="Stundenplan"),
    }


def unique(locator: Any, name: str) -> Any | None:
    visible = locator.filter(visible=True)
    count = visible.count()
    if count > 1:
        raise BootstrapError("ambiguous_control_" + name)
    return visible if count == 1 else None


@dataclass
class AuthFlow:
    credentials: LoginCredentials | None = field(default=None, repr=False)
    manual: bool = False
    trace: Any = field(default=None, repr=False)
    state_seconds: int = 60
    state: str = "starting"
    actions: Counter = field(default_factory=Counter)
    _entered: float = 0
    _acted: bool = False
    _credential_attempts: set[str] = field(default_factory=set, repr=False)
    _entry_attempts: set[str] = field(default_factory=set, repr=False)
    _suppressed_logged: set[str] = field(default_factory=set, repr=False)
    operation: str = "detect"
    last_ui_error_kind: str | None = None
    _credential_steps: dict[str, CredentialStep] = field(default_factory=dict, repr=False)

    def detect(self, page: Any) -> tuple[str, Any | None]:
        url = urlsplit(page.url)
        host = origin(page.url)
        if page.url == "about:blank":
            return "redirect", None
        if host not in TRUSTED:
            raise AuthenticationError("unexpected_authentication_origin")
        c = controls(page)
        if host == PORTAL:
            # The SPA must consume OAuth callbacks itself before navigation.
            if url.query or url.fragment:
                return "redirect", None
            for name in ("timetable", "schedule", "study", "portal_login", "student"):
                item = unique(c[name], name)
                if item is not None:
                    return name, item
        elif host == B2C:
            # Both buttons invoke AzureADExchange in the supplied HTML. Prefer
            # the owner's recorded click; the student button is an evidenced alias.
            for name in ("b2c_microsoft", "b2c_student"):
                item = unique(c[name], name)
                if item is not None:
                    return name, item
        elif host == MICROSOFT:
            # Error ids are referenced by aria-describedby in the captures.
            # Only inspect whether they contain an error, never its contents.
            for name in ("usernameError", "passwordError"):
                item = unique(page.locator("#" + name), name)
                if item is not None and item.inner_text(timeout=1000).strip():
                    raise AuthenticationError("microsoft_rejected_credentials")
            for name in ("kmsi", "password_choice", "password", "email"):
                item = unique(c[name], name)
                if item is not None:
                    return name, item
        elif host == FIDO and url.path == "/common/bridge/fido":
            # Cancel is native browser UI, not a page element. Interactive runs
            # leave it to the operator; --decline-passkey rejects WebAuthn.
            return "native_passkey", None
        return "unknown", None

    def _act(self, locator: Any, host: str, timeout_ms: int,
             *, value: str | None = None, check: bool = False, marker: str | None = None) -> None:
        # Bind the element to its document before filling; a locator could
        # otherwise re-resolve on an untrusted document during a redirect.
        action_deadline = time.monotonic() + timeout_ms / 1000
        self.operation = "resolve_control"
        element = locator.element_handle(timeout=timeout_ms)
        if element is None:
            raise BootstrapError("authentication_control_disappeared")
        try:
            self.operation = "verify_document"
            status = element.evaluate(
                "(node, expected) => node.ownerDocument.location.origin !== expected.host ? "
                "'origin_changed' : (expected.marker && "
                "!node.ownerDocument.querySelector(expected.marker) ? 'form_changed' : 'ready')",
                {"host": host, "marker": marker},
            )
            if status == "origin_changed":
                raise AuthenticationError("authentication_control_origin_changed")
            if status != "ready":
                raise BootstrapError("authentication_form_changed")
            timeout_ms = max(1, int((action_deadline - time.monotonic()) * 1000))
            if value is not None:
                self.operation = "fill_control"
                element.fill(value, timeout=timeout_ms)
            elif check:
                self.operation = "check_control"
                element.set_checked(True, timeout=timeout_ms)
            else:
                self.operation = "click_control"
                element.click(timeout=timeout_ms)
        finally:
            # Handle disposal is cleanup, not evidence that the action failed.
            # A destroyed document must not mask an earlier operation's error.
            with suppress(Exception):
                element.dispose()

    def advance(self, page: Any, *, now: float, timeout_ms: int) -> bool:
        self.operation = "detect"
        state, item = self.detect(page)
        for phase, step in self._credential_steps.items():
            if state not in {phase, "unknown", "redirect"}:
                step.progressed, step.active = True, False
        if state != self.state:
            self.state, self._entered, self._acted = state, now, False
            if self.trace:
                self.trace("auth_state", state=state)
        if now - self._entered >= self.state_seconds and not self.manual:
            raise BootstrapError("authentication_state_timeout_" + state)
        if self._acted or state in {"redirect", "unknown", "native_passkey", "timetable"}:
            return False
        entry = ENTRY_ACTIONS.get(state)
        if entry is not None and entry in self._entry_attempts:
            if self.trace and state not in self._suppressed_logged:
                self.trace("auth_action_suppressed", state=state)
                self._suppressed_logged.add(state)
            return False  # Await the portal-owned redirect/render, within existing timeouts.
        if state in {"email", "password"} and self.credentials is None:
            if not self.manual:
                raise AuthenticationError("session_requires_interactive_login")
            return False
        # Submit only in the presence of its particular form. No generic Yes or
        # consent handling. Wet login may begin directly at password or KMSI.
        submit = None
        if state in {"email", "password", "kmsi"}:
            submit = unique(controls(page)["submit"], "submit")
            if submit is None:
                return False
        if state in {"email", "password"}:
            if state in self._credential_attempts:
                previous = self._credential_steps.get(state)
                if previous is not None and not previous.progressed:
                    return False  # Await an uncertain/in-flight outcome, never resubmit.
                raise AuthenticationError("credential_form_repeated_" + state)
            self._credential_attempts.add(state)
        if self.actions[state] >= 2:
            raise BootstrapError("authentication_transition_limit_" + state)
        self._acted = True  # Never repeat a partially completed action.
        if entry is not None:
            self._entry_attempts.add(entry)
        self.actions[state] += 1
        if self.trace:
            self.trace("auth_action", state=state)
        # Preserve the detected state's expected origin across redirect races.
        host = (MICROSOFT if state in {"email", "password", "kmsi", "password_choice"}
                else B2C if state in {"b2c_microsoft", "b2c_student"} else PORTAL)
        action_deadline = time.monotonic() + timeout_ms / 1000

        def remaining():
            return max(1, int((action_deadline - time.monotonic()) * 1000))

        if state in {"email", "password"}:
            value = self.credentials.username if state == "email" else self.credentials.password
            step = CredentialStep(
                page=page, phase=state, marker=EMAIL if state == "email" else PASSWORD, host=host,
                operation=lambda value: setattr(self, "operation", value),
                on_error=lambda error: self.record_ui_error(error, TimeoutError), trace=self.trace,
            )
            self._credential_steps[state] = step
            step.run(value, remaining())
        elif state == "kmsi":
            checkbox = unique(controls(page)["dont_show_again"], "dont_show_again")
            if checkbox is not None:
                self._act(checkbox, host, remaining(), check=True)
            self._act(submit, host, remaining(), marker="#KmsiDescription")
        else:
            self._act(item, host, remaining())
        if self.trace:
            self.trace("auth_action_completed", state=state)
        return True

    def after_expiry(self) -> None:
        # The browser driver permits one explicit 401 recovery. Start its entry
        # sequence afresh; cumulative state/credential budgets survive.
        self.state, self._entered, self._acted = "starting", 0, False
        self._entry_attempts.clear()
        self._suppressed_logged.clear()
        for step in self._credential_steps.values():
            step.progressed, step.active = True, False

    def observe_request(self, request: Any) -> None:
        for step in self._credential_steps.values():
            step.observe_request(request)

    def record_ui_error(self, error: Exception, timeout_type: type) -> str:
        kind = ui_error_kind(error, timeout_type)
        self.last_ui_error_kind = kind
        if self.trace:
            self.trace("auth_ui_error", state=self.state,
                       operation=self.operation, error_kind=kind)
        return kind

    def diagnostics(self) -> dict:
        result = {"auth_state": self.state, "auth_actions": dict(self.actions),
                  "auth_operation": self.operation,
                  "credential_submissions_observed": {
                      phase: int(step.observed) for phase, step in self._credential_steps.items()
                  },
                  "credential_submission_outcomes": {
                      phase: step.outcome for phase, step in self._credential_steps.items()
                  },
                  "credential_control_recoveries": {
                      phase: step.recoveries for phase, step in self._credential_steps.items()
                  }}
        if self.last_ui_error_kind is not None:
            result["last_ui_error_kind"] = self.last_ui_error_kind
        return result


# A document-local counter wakes the driver on real DOM changes; no sleeps or
# networkidle assumption. It stores no DOM text, URL, cookie, or credential.
DOM_WATCH = """
(() => {
  if (window.__otterM1Watch) return;
  window.__otterM1Watch = {revision: 0};
  new MutationObserver(() => window.__otterM1Watch.revision++)
    .observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
})();
"""


def wait_for_change(page: Any, timeout_ms: int, pw_timeout: type, pw_error: type) -> None:
    """Wait for document/DOM changes; bounded heartbeat also pumps API events."""
    try:
        page.evaluate(DOM_WATCH)
        stamp = page.evaluate(
            "() => [performance.timeOrigin, window.__otterM1Watch.revision, location.pathname]"
        )
        page.wait_for_function(
            "s => performance.timeOrigin !== s[0] || location.pathname !== s[2] || "
            "!window.__otterM1Watch || window.__otterM1Watch.revision !== s[1]",
            arg=stamp, timeout=min(1000, timeout_ms),
        )
    except pw_timeout:
        pass  # Bounded event heartbeat, not an authentication retry.
    except pw_error as error:
        if page.is_closed():
            raise AuthenticationError("browser_closed_before_authentication") from None
        if ui_error_kind(error, pw_timeout) != "context_destroyed":
            raise BootstrapError("authentication_wait_failed") from None
        # A cross-document redirect destroyed this wait. Re-detect under the
        # same global deadline without repeating already attempted actions.
