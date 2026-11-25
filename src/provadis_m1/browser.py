"""Let the existing portal obtain credentials; replay only the exact API GET.

No OAuth client, token cache reader, refresh-token replay, tracing, or storage export.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .auth_flow import TRUSTED, AuthFlow, origin, wait_for_change
from .credentials import LoginCredentials
from .errors import (
    AuthenticationError,
    BootstrapError,
    ConfigurationError,
    InvalidResponse,
    M1Error,
    NetworkError,
)
from .failure_diagnostics import capture_failure
from .navigation_log import attach_navigation_log
from .validation import MAX_RESPONSE_BYTES, ValidatedSnapshot, validate_http, validate_response

DEFAULT_PORTAL_PATH = "/student/schedule"
PORTAL_URL = "https://hvsweb.provadis-hochschule.de/"
API_URL = "https://api.provadis-hochschule.de/api/v1/Student/GetEventList"
IDENTITY_HOST = "ishgroupb2c.b2clogin.com"


def portal_url(path: str) -> str:
    # Allow a confirmed SPA route, but never alternate hosts, OAuth query strings,
    # authorization codes, or token-bearing fragments in configuration/arguments.
    if not re.fullmatch(r"/[A-Za-z0-9_./-]*(?:#[A-Za-z0-9_./-]*)?", path):
        raise ConfigurationError("portal_path_must_be_a_same_origin_route_without_parameters")
    if path.startswith("//") or "/../" in path or path.endswith("/.."):
        raise ConfigurationError("invalid_portal_path")
    return PORTAL_URL.rstrip("/") + path


@dataclass
class PortalCapture:
    bearer: str | None = field(default=None, repr=False)
    api_requests: int = 0
    identity_requests: int = 0
    token_endpoint_requests: int = 0
    last_api_status: int | None = None
    direct_status: int | None = None
    api_transport_failed: bool = False
    page_errors: int = 0

    def observe_page_error(self, _error: Any) -> None:
        # Count only: JavaScript error messages/stacks may contain sensitive data.
        self.page_errors += 1

    def diagnostics(self, final_url: str, *, headed: bool) -> dict[str, Any]:
        parsed = urlsplit(final_url)
        if parsed.hostname == urlsplit(PORTAL_URL).hostname:
            location = "portal"
        elif parsed.hostname == IDENTITY_HOST:
            location = "identity_provider"
        elif parsed.hostname in {"login.microsoftonline.com", "login.microsoft.com", "login.live.com"}:
            location = "microsoft_login"
        elif final_url == "about:blank":
            location = "blank"
        else:
            location = "other"
        return {
            "browser_mode": "headed" if headed else "headless",
            "final_location": location,
            "portal_api_requests_observed": self.api_requests,
            "last_portal_api_status": self.last_api_status,
            "last_direct_api_status": self.direct_status,
            "portal_api_transport_failed": self.api_transport_failed,
            "identity_requests_observed": self.identity_requests,
            "token_endpoint_requests_observed": self.token_endpoint_requests,
            "page_error_count": self.page_errors,
            "timetable_bearer_observed": self.bearer is not None,
        }

    def observe(self, request: Any) -> None:
        """Never capture arbitrary Authorization headers, URLs, bodies, or cookies."""
        parsed = urlsplit(request.url)
        if parsed.hostname == IDENTITY_HOST:
            self.identity_requests += 1
            if parsed.path.rstrip("/").endswith("/token"):
                self.token_endpoint_requests += 1
        if request.method != "GET" or request.url != API_URL:
            return
        self.api_requests += 1

    def observe_response(self, response: Any) -> None:
        request = response.request
        if request.method != "GET" or response.url != API_URL or request.url != API_URL:
            return
        self.last_api_status = response.status
        # An initial stale-token request can fail while the portal silently renews.
        # Capture the header associated with a subsequent HTTP 200, not the stale one.
        if response.status != 200:
            return
        value = request.header_value("authorization")
        if isinstance(value, str) and value.startswith("Bearer "):
            if value[7:].strip() and "\n" not in value and "\r" not in value:
                self.bearer = value

    def observe_failed(self, request: Any) -> None:
        if request.method == "GET" and request.url == API_URL:
            self.api_transport_failed = True

    def require_bearer(self) -> str:
        if self.bearer is not None:
            return self.bearer
        if self.last_api_status is not None:
            validate_http(self.last_api_status, "application/json")
        if self.api_transport_failed:
            raise NetworkError("portal_timetable_request_failed")
        raise BootstrapError()


def direct_fetch(context: Any, bearer: str, timeout_ms: int, pw_error: type,
                 capture: PortalCapture | None = None) -> ValidatedSnapshot:
    """No redirects, retries, parameters, request body, or UI navigation."""
    try:
        response = context.request.get(
            API_URL,
            headers={"Authorization": bearer, "Accept": "application/json"},
            timeout=timeout_ms,
            max_redirects=0,
            max_retries=0,
            fail_on_status_code=False,
        )
    except pw_error:
        raise NetworkError() from None
    try:
        if capture is not None:
            capture.direct_status = response.status
        if response.url != API_URL:
            raise InvalidResponse("unexpected_response_url")
        content_type = response.headers.get("content-type", "")
        validate_http(response.status, content_type)
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > MAX_RESPONSE_BYTES:
                    raise InvalidResponse("invalid_response_size")
            except ValueError:
                raise InvalidResponse("invalid_content_length") from None
        return validate_response(response.status, content_type, response.body())
    except pw_error:
        raise NetworkError("response_read_failed") from None
    finally:
        response.dispose()


def browser_run(
    profile: Path,
    *,
    interactive: bool,
    bootstrap_seconds: int,
    request_seconds: int,
    samples: int,
    sample_interval: int,
    portal_path: str = DEFAULT_PORTAL_PATH,
    headed: bool = False,
    trace: Any = None,
    credentials: LoginCredentials | None = None,
    decline_passkey: bool = False,
    failure_pause_seconds: int = 0,
    keep_open_on_error: bool = False,
    state_seconds: int = 60,
) -> dict[str, Any]:
    # Import lazily so unit tests need no browser installation.
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ConfigurationError("playwright_not_installed") from None

    # The captured route is the only implemented target; never silently ignore
    # a custom route supplied by a caller.
    if portal_path != DEFAULT_PORTAL_PATH:
        raise ConfigurationError("only_captured_timetable_route_supported")
    capture = PortalCapture()
    visible = interactive or headed
    flow = AuthFlow(credentials=credentials, manual=visible, trace=trace,
                    state_seconds=state_seconds)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=not visible, channel="chromium",
                chromium_sandbox=True, accept_downloads=False,
                timeout=request_seconds * 1000,
            )
        except PlaywrightError:
            raise ConfigurationError("browser_launch_failed") from None
        page = None
        recovering = False
        deadline = time.monotonic() + bootstrap_seconds
        pages = []

        def attach(new_page: Any) -> None:
            if new_page in pages:
                return
            pages.append(new_page)
            new_page.on("pageerror", capture.observe_page_error)
            if trace:
                attach_navigation_log(new_page, trace)

        def active_page() -> Any:
            # Normal flow stays in one tab. A legitimate SSO popup, if present,
            # may start blank; never fill credentials there before origin checks.
            nonlocal page
            opened = [p for p in pages if not p.is_closed()]
            if not opened:
                raise AuthenticationError("browser_closed_before_authentication")
            if page not in opened or len(opened) > 1:
                candidate = opened[-1]
                if candidate.url != "about:blank" and origin(candidate.url) not in TRUSTED:
                    raise AuthenticationError("unexpected_authentication_origin")
                page = candidate
            return page

        def go_home() -> None:
            nonlocal page
            page = active_page()
            if trace:
                trace("goto_attempt", action="portal_start", url=PORTAL_URL)
            try:
                page.goto(PORTAL_URL, wait_until="domcontentloaded",
                          timeout=max(1, min(request_seconds * 1000,
                              int((deadline - time.monotonic()) * 1000))))
            except PlaywrightError:
                raise NetworkError("portal_navigation_failed") from None
            if trace:
                trace("goto_completed", action="portal_start", url=page.url)

        def recover_expired() -> None:
            nonlocal recovering, deadline
            if recovering:
                raise AuthenticationError("session_reauthentication_failed")
            recovering = True
            deadline = time.monotonic() + bootstrap_seconds
            capture.bearer = None
            capture.last_api_status = None
            flow.after_expiry()
            if trace:
                trace("session_reauthentication")
            # Restart the portal's normal UI once, never replay OAuth URLs or
            # read refresh tokens. An expired profile follows the same detector.
            go_home()

        def bootstrap() -> None:
            while capture.bearer is None:
                current = active_page()
                now = time.monotonic()
                if now >= deadline:
                    if capture.last_api_status == 401 and not recovering:
                        recover_expired()
                        continue
                    if capture.last_api_status in (401, 403):
                        raise AuthenticationError("api_access_denied")
                    if capture.api_transport_failed:
                        raise NetworkError("portal_timetable_request_failed")
                    if capture.last_api_status is not None:
                        validate_http(capture.last_api_status, "application/json")
                    raise BootstrapError("authentication_timeout_" + flow.state)
                if trace:
                    trace.observe_url(current.url)
                remaining = max(1, int((deadline - now) * 1000))
                # A popup may redirect during detection; retry only destroyed
                # execution contexts, not arbitrary failed clicks/fills.
                try:
                    flow.advance(current, now=now,
                                 timeout_ms=min(request_seconds * 1000, remaining))
                except BootstrapError as error:
                    if (error.reason == "authentication_state_timeout_timetable"
                            and capture.last_api_status == 401 and not recovering):
                        recover_expired()
                        continue
                    raise
                except PlaywrightError as error:
                    kind = flow.record_ui_error(error, PlaywrightTimeout)
                    if current.is_closed():
                        active_page()
                        continue
                    if kind == "context_destroyed":
                        # Redirect raced a state probe. Any attempted action is
                        # already marked, so re-detection cannot blindly repeat it.
                        continue
                    raise BootstrapError("authentication_ui_operation_failed") from None
                if capture.bearer is not None:
                    break
                # Give the portal its normal bootstrap budget to renew a stale
                # session before restarting it. A 401 can precede a valid GET.
                flow.operation = "wait_for_change"
                wait_for_change(current, remaining, PlaywrightTimeout, PlaywrightError)

        try:
            from .credential_step import CREDENTIAL_CAPTURE_SCRIPT
            context.add_init_script(CREDENTIAL_CAPTURE_SCRIPT)

            def observe_request(request: Any) -> None:
                capture.observe(request)
                flow.observe_request(request)

            context.on("request", observe_request)
            context.on("response", capture.observe_response)
            context.on("requestfailed", capture.observe_failed)
            context.on("page", attach)
            # Persistent contexts can restore several tabs. Close extras before
            # starting, avoiding stale timetable responses from another tab.
            page = context.pages[0] if context.pages else context.new_page()
            for extra in context.pages[1:]:
                extra.close()
            attach(page)
            if decline_passkey:
                from .passkey import DECLINE_PASSKEY_SCRIPT
                context.add_init_script(DECLINE_PASSKEY_SCRIPT)
                if trace:
                    trace("passkey_decline_enabled")
            if trace:
                trace("browser_started")
            go_home()
            bootstrap()
            counts = []
            identity_before_direct = capture.identity_requests
            for sample in range(samples):
                if sample and sample_interval:
                    # This is a user-requested measurement interval, not a UI delay.
                    active_page().wait_for_timeout(sample_interval * 1000)
                if trace:
                    trace("api_attempt", action="direct_api", url=API_URL)
                try:
                    snapshot = direct_fetch(context, capture.require_bearer(),
                                            request_seconds * 1000, PlaywrightError, capture)
                except AuthenticationError as error:
                    if error.reason != "api_access_denied" or recovering:
                        raise
                    # Only 401 denotes an expired candidate session. A 403 can
                    # mean missing permission; do not turn that into a login loop.
                    if capture.direct_status != 401:
                        raise
                    recover_expired()
                    bootstrap()
                    snapshot = direct_fetch(context, capture.require_bearer(),
                                            request_seconds * 1000, PlaywrightError, capture)
                counts.append(snapshot.event_count)
                if trace:
                    trace("api_validated", action="direct_api", status=200)
            return {
                "diagnostics": {**capture.diagnostics(active_page().url, headed=visible),
                                **flow.diagnostics(), "session_reauthentication": recovering},
                "direct_api_samples": len(counts), "event_counts": counts,
                "portal_bootstrap_used": True,
                "portal_api_requests_observed": capture.api_requests,
                "identity_requests_observed": capture.identity_requests,
                "token_endpoint_requests_observed": capture.token_endpoint_requests,
                "identity_requests_during_direct_samples": (
                    capture.identity_requests - identity_before_direct),
            }
        except (M1Error, PlaywrightError) as original:
            error = (original if isinstance(original, M1Error)
                     else BootstrapError("browser_operation_failed"))
            if trace:
                trace("run_failed", url=page.url if page else "about:blank")
            error.diagnostics = {
                **capture.diagnostics(page.url if page else "about:blank", headed=visible),
                **flow.diagnostics(), "session_reauthentication": recovering,
            }
            if page is not None:
                try:
                    error.diagnostics["failure_artifacts"] = capture_failure(
                        page, profile.parent, error.diagnostics.copy(), PlaywrightError)
                except (OSError, PlaywrightError):
                    error.diagnostics["failure_artifacts_unavailable"] = True
            if visible and page is not None and not page.is_closed():
                if keep_open_on_error:
                    # Explicit debugger mode: closing the window or Ctrl+C ends
                    # inspection. The profile lock remains held during inspection.
                    if trace:
                        trace("failure_pause")
                    try:
                        page.wait_for_event("close", timeout=0)
                    except PlaywrightError:
                        pass
                elif failure_pause_seconds:
                    try:
                        page.wait_for_timeout(min(failure_pause_seconds, 60) * 1000)
                    except PlaywrightError:
                        pass
            raise error from None
        finally:
            capture.bearer = None
            try:
                context.close()
            except PlaywrightError:
                pass  # Do not replace the original failure with cleanup errors.
            if trace:
                trace("browser_finished")
