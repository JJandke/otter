"""Bounded portal navigation, without interacting with identity-provider forms."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import BootstrapError

DEFAULT_PORTAL_PATH = "/student/schedule"


@dataclass
class StudentNavigation:
    student_entry_clicked: bool = False
    schedule_navigation_attempted: bool = False
    student_page_seen_at: float | None = None

    def advance(
        self, page: Any, *, portal_origin: str, target_url: str,
        now: float, timeout_ms: int, pw_error: type,
    ) -> None:
        current = urlsplit(page.url)
        origin = urlsplit(portal_origin)
        target = urlsplit(target_url)
        # Never inspect/click on identity-provider pages or OAuth callback URLs.
        if (current.scheme, current.netloc) != (origin.scheme, origin.netloc):
            self.student_page_seen_at = None
            return
        if current.query or current.fragment:
            self.student_page_seen_at = None
            return
        if current.path in ("", "/"):
            self.student_page_seen_at = None
            if self.student_entry_clicked:
                return
            # The public DOM also contains a hidden sidebar with the same text.
            # Select the visible card heading, never an arbitrary first text match.
            entry = page.get_by_role(
                "heading", name="Für Studierende", level=2, exact=True
            ).filter(visible=True)
            count = entry.count()
            if count > 1:
                raise BootstrapError("ambiguous_student_entry")
            if count == 1:
                self.student_entry_clicked = True
                try:
                    entry.click(timeout=timeout_ms)
                except pw_error:
                    raise BootstrapError("student_entry_click_failed") from None
            return
        if not (current.path == "/student" or current.path.startswith("/student/")):
            self.student_page_seen_at = None
            return
        if current.path == target.path and current.fragment == target.fragment:
            return
        if not target.path.startswith("/student/") or self.schedule_navigation_attempted:
            return
        # Give the SPA's post-SSO callback time to settle before one navigation.
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
            "schedule_navigation_attempted": self.schedule_navigation_attempted,
        }
