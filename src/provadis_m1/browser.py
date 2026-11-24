"""Let the existing portal obtain credentials; replay only the exact API GET.

No OAuth client, token cache reader, refresh-token replay, tracing, or storage export.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import (
    AuthenticationError, BootstrapError, ConfigurationError, InvalidResponse, M1Error, NetworkError,
)
from .validation import MAX_RESPONSE_BYTES, ValidatedSnapshot, validate_http, validate_response
from .navigation import DEFAULT_PORTAL_PATH, StudentNavigation
from .navigation_log import attach_navigation_log
from .credentials import LoginCredentials
from .password_login import PasswordLogin

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
        elif parsed.hostname in {"login.microsoftonline.com", "login.live.com"}:
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


def direct_fetch(context: Any, bearer: str, timeout_ms: int, pw_error: type) -> ValidatedSnapshot:
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
) -> dict[str, Any]:
    # Lazy import keeps boundary tests runnable using only Python's standard library.
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ConfigurationError("playwright_not_installed") from None

    capture = PortalCapture()
    navigation = StudentNavigation(trace=trace)
    password_login = PasswordLogin(credentials, trace=trace) if credentials else None
    visible = interactive or headed
    try:
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=not visible,
                    channel="chromium",
                    chromium_sandbox=True,
                    accept_downloads=False,
                    timeout=request_seconds * 1000,
                )
            except PlaywrightError:
                raise ConfigurationError("browser_launch_failed") from None
            page = None
            try:
                if decline_passkey:
                    from .passkey import DECLINE_PASSKEY_SCRIPT
                    context.add_init_script(DECLINE_PASSKEY_SCRIPT)
                    if trace:
                        trace('passkey_decline_enabled')
                context.on("request", capture.observe)
                context.on("response", capture.observe_response)
                context.on("requestfailed", capture.observe_failed)
                page = context.pages[0] if context.pages else context.new_page()
                page.on("pageerror", capture.observe_page_error)
                target_url = portal_url(portal_path)

                def observe_main_frame(frame: Any) -> None:
                    if frame == page.main_frame:
                        navigation.observe_location(
                            frame.url, portal_origin=PORTAL_URL, target_url=target_url
                        )

                # Observe even brief SPA route changes, independently of logging.
                page.on("framenavigated", observe_main_frame)
                if trace:
                    trace("browser_started")
                    attach_navigation_log(page, trace)
                    trace("goto_attempt", action="portal_start", url=PORTAL_URL)
                try:
                    page.goto(
                        PORTAL_URL,
                        wait_until="domcontentloaded", timeout=request_seconds * 1000
                    )
                except PlaywrightError:
                    if trace:
                        trace("goto_failed", action="portal_start", url=page.url)
                    raise NetworkError("portal_navigation_failed") from None

                if trace:
                    trace("goto_completed", action="portal_start", url=page.url)
                deadline = time.monotonic() + bootstrap_seconds
                while capture.bearer is None and time.monotonic() < deadline:
                    if trace:
                        trace.observe_url(page.url)
                    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                    if password_login:
                        password_login.advance(
                            page, timeout_ms=min(request_seconds * 1000, remaining_ms),
                            pw_error=PlaywrightError,
                        )
                    navigation.advance(
                        page, portal_origin=PORTAL_URL, target_url=target_url,
                        now=time.monotonic(),
                        timeout_ms=min(request_seconds * 1000, remaining_ms),
                        pw_error=PlaywrightError,
                    )
                    try:
                        page.wait_for_timeout(200)
                    except PlaywrightError:
                        raise AuthenticationError("browser_closed_before_authentication") from None
                if (password_login and capture.bearer is None and capture.api_requests == 0):
                    raise BootstrapError('headless_login_incomplete')
                capture.require_bearer()

                counts = []
                identity_before_direct = capture.identity_requests
                for sample in range(samples):
                    if sample:
                        # Pump browser events while waiting, allowing the portal to renew naturally.
                        page.wait_for_timeout(sample_interval * 1000)
                    if trace:
                        trace("api_attempt", action="direct_api", url=API_URL)
                    snapshot = direct_fetch(
                        context, capture.require_bearer(), request_seconds * 1000, PlaywrightError
                    )
                    counts.append(snapshot.event_count)
                    if trace:
                        trace("api_validated", action="direct_api", status=200)
                return {
                    "diagnostics": {
                        **capture.diagnostics(page.url, headed=visible),
                        **navigation.diagnostics(),
                        **(password_login.diagnostics() if password_login else {}),
                    },
                    "direct_api_samples": len(counts),
                    "event_counts": counts,
                    "portal_bootstrap_used": True,
                    "portal_api_requests_observed": capture.api_requests,
                    "identity_requests_observed": capture.identity_requests,
                    "token_endpoint_requests_observed": capture.token_endpoint_requests,
                    "identity_requests_during_direct_samples": (
                        capture.identity_requests - identity_before_direct
                    ),
                }
            except M1Error as error:
                if trace:
                    trace("run_failed", url=page.url if page else "about:blank")
                error.diagnostics = capture.diagnostics(
                    page.url if page is not None else "about:blank", headed=visible
                )
                error.diagnostics.update(navigation.diagnostics())
                if password_login:
                    error.diagnostics.update(password_login.diagnostics())
                raise
            finally:
                capture.bearer = None
                context.close()
                if trace:
                    trace("browser_finished")
    except PlaywrightError:
        # Raw Playwright errors may contain request headers, URLs, or browser logs.
        raise ConfigurationError("browser_operation_failed") from None
