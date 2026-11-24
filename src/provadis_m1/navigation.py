"""Bounded portal/SSO routing; never enter credentials or handle MFA prompts."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import BootstrapError

DEFAULT_PORTAL_PATH = "/student/schedule"
B2C_ORIGIN = "https://ishgroupb2c.b2clogin.com"


@dataclass
class StudentNavigation:
    student_entry_clicked: bool = False
    sign_in_clicked: bool = False
    student_sso_clicked: bool = False
    my_studium_clicked: bool = False
    timetable_link_clicked: bool = False
    schedule_navigation_attempted: bool = False
    student_page_seen_at: float | None = None

    def _click_once(self, locator: Any, flag: str, timeout_ms: int, pw_error: type) -> bool:
        if getattr(self, flag):
            return False
        visible = locator.filter(visible=True)
        count = visible.count()
        if count > 1:
            raise BootstrapError("ambiguous_" + flag.removesuffix("_clicked"))
        if count == 0:
            return False
        setattr(self, flag, True)
        try:
            visible.click(timeout=timeout_ms)
        except pw_error:
            raise BootstrapError(flag.removesuffix("_clicked") + "_click_failed") from None
        return True

    def advance(
        self, page: Any, *, portal_origin: str, target_url: str,
        now: float, timeout_ms: int, pw_error: type,
    ) -> None:
        current = urlsplit(page.url)
        origin = urlsplit(portal_origin)
        target = urlsplit(target_url)
        current_origin = (current.scheme, current.netloc)
        b2c = urlsplit(B2C_ORIGIN)
        if current_origin == (b2c.scheme, b2c.netloc):
            self.student_page_seen_at = None
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
            self.student_page_seen_at = None
            return  # Microsoft email/password/passkey/MFA/stay-signed-in remain manual.
        if current.query or current.fragment:
            self.student_page_seen_at = None
            return  # Leave portal OAuth callback handling to the original SPA.
        if current.path.rstrip("/") == "/login/student":
            self.student_page_seen_at = None
            if not self.sign_in_clicked:
                self._click_once(
                    page.get_by_role("button", name="Jetzt anmelden", exact=True),
                    "sign_in_clicked", timeout_ms, pw_error,
                )
            return
        is_landing = current.path in ("", "/")
        if is_landing and not self.student_entry_clicked and not self.student_sso_clicked:
            self.student_page_seen_at = None
            self._click_once(
                page.get_by_role("heading", name="Für Studierende", level=2, exact=True),
                "student_entry_clicked", timeout_ms, pw_error,
            )
            return
        is_student_page = current.path == "/student" or current.path.startswith("/student/")
        if not is_student_page and not (is_landing and self.student_sso_clicked):
            self.student_page_seen_at = None
            return
        if current.path == target.path and current.fragment == target.fragment:
            return
        if not target.path.startswith("/student/"):
            return
        # The two portal menu labels were reported by the owner. Their actual DOM
        # selectors still need live verification; only unique visible matches click.
        if target.path == DEFAULT_PORTAL_PATH:
            if not self.my_studium_clicked and self._click_once(
                page.get_by_text("My Studium", exact=True), "my_studium_clicked",
                timeout_ms, pw_error,
            ):
                return
            if not self.timetable_link_clicked and self._click_once(
                page.get_by_text("Stundenplan", exact=True), "timetable_link_clicked",
                timeout_ms, pw_error,
            ):
                return
        if self.schedule_navigation_attempted:
            return
        # Retain one direct timetable navigation as fallback after portal return.
        # Delay is stabilization only, not a claim that authentication succeeded.
        if self.student_page_seen_at is None:
            self.student_page_seen_at = now
            return
        if now - self.student_page_seen_at < 2:
            return
        self.schedule_navigation_attempted = True
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
        except pw_error:
            raise BootstrapError("post_signin_schedule_navigation_failed") from None

    def diagnostics(self) -> dict[str, bool]:
        return {
            "student_entry_clicked": self.student_entry_clicked,
            "sign_in_clicked": self.sign_in_clicked,
            "student_sso_clicked": self.student_sso_clicked,
            "my_studium_clicked": self.my_studium_clicked,
            "timetable_link_clicked": self.timetable_link_clicked,
            "schedule_navigation_attempted": self.schedule_navigation_attempted,
        }
