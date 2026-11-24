"""Bounded portal/SSO routing; never enter credentials or handle MFA prompts."""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .errors import BootstrapError

DEFAULT_PORTAL_PATH = "/student/schedule"
B2C_ORIGIN = "https://ishgroupb2c.b2clogin.com"


@dataclass
class StudentNavigation:
    trace: Any = field(default=None, repr=False)
    student_entry_clicked: bool = False
    post_sso_student_entry_clicked: bool = False
    missing_selectors_logged: set[str] = field(default_factory=set, repr=False)
    sign_in_clicked: bool = False
    student_sso_clicked: bool = False
    my_studium_clicked: bool = False
    timetable_link_clicked: bool = False
    schedule_navigation_attempted: bool = False
    dashboard_retries: int = 0
    _schedule_route_seen: bool = field(default=False, repr=False)

    def observe_location(self, url: str, *, portal_origin: str, target_url: str) -> None:
        """Observe routes only; a timetable URL is not proof of valid API data."""
        current, origin, target = map(urlsplit, (url, portal_origin, target_url))
        if ((current.scheme, current.netloc) == (origin.scheme, origin.netloc)
                and current.path == target.path == DEFAULT_PORTAL_PATH
                and not current.query and not current.fragment
                and self.timetable_link_clicked):
            self._schedule_route_seen = True

    def _recover_dashboard(self, page: Any) -> None:
        # A logged run reached /student/schedule, then the portal replaced it
        # with /. Retry only when the confirmed authenticated card reappears.
        # No timers, document reloads, SSO replay, or unlimited click loops.
        if (not self._schedule_route_seen or not self.timetable_link_clicked
                or self.dashboard_retries >= 1):
            return
        count = page.get_by_text("Mein Studium", exact=True).filter(visible=True).count()
        if count > 1:
            if self.trace:
                self.trace("selector_ambiguous", action="my_studium_clicked", matches=count)
            raise BootstrapError("ambiguous_my_studium")
        if count == 0:
            return
        self.dashboard_retries += 1
        self._schedule_route_seen = False
        self.my_studium_clicked = False
        self.timetable_link_clicked = False
        if self.trace:
            self.trace("dashboard_retry")

    def _click_once(self, locator: Any, flag: str, timeout_ms: int, pw_error: type) -> bool:
        if getattr(self, flag):
            return False
        visible = locator.filter(visible=True)
        count = visible.count()
        if count > 1:
            if self.trace:
                self.trace("selector_ambiguous", action=flag, matches=count)
            raise BootstrapError("ambiguous_" + flag.removesuffix("_clicked"))
        if count == 0:
            if self.trace and flag not in self.missing_selectors_logged:
                self.trace("selector_missing", action=flag, matches=0)
                self.missing_selectors_logged.add(flag)
            return False
        setattr(self, flag, True)
        if self.trace:
            self.trace("click_attempt", action=flag, matches=count)
        try:
            visible.click(timeout=timeout_ms)
        except pw_error:
            if self.trace:
                self.trace("click_failed", action=flag)
            raise BootstrapError(flag.removesuffix("_clicked") + "_click_failed") from None
        if self.trace:
            self.trace("click_completed", action=flag)
        return True

    def advance(
        self, page: Any, *, portal_origin: str, target_url: str,
        now: float, timeout_ms: int, pw_error: type,
    ) -> None:
        current = urlsplit(page.url)
        self.observe_location(page.url, portal_origin=portal_origin, target_url=target_url)
        origin = urlsplit(portal_origin)
        target = urlsplit(target_url)
        current_origin = (current.scheme, current.netloc)
        b2c = urlsplit(B2C_ORIGIN)
        if current_origin == (b2c.scheme, b2c.netloc):
            # This routing option was inspected on the public B2C page. OAuth
            # query parameters are expected here but are never read or reported.
            # Do not choose the generic Microsoft option in the applicant section.
            if self.sign_in_clicked and not self.student_sso_clicked:
                self._click_once(
                    page.get_by_role("button", name="Anmeldung für Studierende und Lehrende",
                                     exact=True),
                    "student_sso_clicked", timeout_ms, pw_error,
                )
            return
        if current_origin != (origin.scheme, origin.netloc):
            return  # Microsoft email/password/passkey/MFA/stay-signed-in remain manual.
        if current.query or current.fragment:
            return  # Leave portal OAuth callback handling to the original SPA.
        if current.path.rstrip("/") == "/login/student":
            if not self.sign_in_clicked:
                self._click_once(
                    page.get_by_role("button", name="Jetzt anmelden", exact=True),
                    "sign_in_clicked", timeout_ms, pw_error,
                )
            return
        is_landing = current.path in ("", "/")
        if is_landing and not self.student_entry_clicked and not self.student_sso_clicked:
            self._click_once(
                page.get_by_role("heading", name="Für Studierende", level=2, exact=True),
                "student_entry_clicked", timeout_ms, pw_error,
            )
            return
        is_student_page = current.path == "/student" or current.path.startswith("/student/")
        if not is_student_page and not (is_landing and self.student_sso_clicked):
            return
        if current.path == target.path and current.fragment == target.fragment:
            return
        if not target.path.startswith("/student/"):
            return
        if is_landing:
            self._recover_dashboard(page)
        # The two portal menu labels were reported by the owner. Their actual DOM
        # selectors still need live verification; only unique visible matches click.
        if target.path == DEFAULT_PORTAL_PATH:
            if not self.my_studium_clicked and self._click_once(
                page.get_by_text("Mein Studium", exact=True), "my_studium_clicked",
                timeout_ms, pw_error,
            ):
                return
            if not self.timetable_link_clicked and self._click_once(
                page.get_by_text("Stundenplan", exact=True), "timetable_link_clicked",
                timeout_ms, pw_error,
            ):
                return
        if is_landing and self.student_sso_clicked and not self.post_sso_student_entry_clicked:
            # SSO selection is not proof of API authorization. Re-enter the visible
            # student card once, in the current SPA document; never reload it here.
            self._click_once(
                page.get_by_role("heading", name="Für Studierende", level=2, exact=True),
                "post_sso_student_entry_clicked", timeout_ms, pw_error,
            )
        # No page.goto fallback: a full document reload returned to the landing
        # page in the owner's trace. Wait for portal navigation or fail closed.

    def diagnostics(self) -> dict[str, bool | int]:
        return {
            "student_entry_clicked": self.student_entry_clicked,
            "post_sso_student_entry_clicked": self.post_sso_student_entry_clicked,
            "sign_in_clicked": self.sign_in_clicked,
            "student_sso_clicked": self.student_sso_clicked,
            "my_studium_clicked": self.my_studium_clicked,
            "timetable_link_clicked": self.timetable_link_clicked,
            "schedule_navigation_attempted": self.schedule_navigation_attempted,
            "dashboard_retries": self.dashboard_retries,
        }
