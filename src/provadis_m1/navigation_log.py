"""Opt-in allowlisted JSONL diagnostics; never a HAR or browser trace."""

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .auth_flow import UI_ERROR_KINDS, UI_OPERATIONS

EVENTS = {
    'auth_state', 'auth_action', 'auth_action_completed', 'session_reauthentication',
    'auth_action_suppressed', 'auth_ui_error',
    'credential_request_observed', 'credential_control_reacquired', 'credential_submission_uncertain',
    'log_started', 'log_finished', 'browser_started', 'browser_finished',
    'click_attempt', 'click_completed', 'click_failed', 'selector_ambiguous',
    'goto_attempt', 'goto_completed', 'goto_failed', 'frame_navigated',
    'navigation_request', 'navigation_response', 'navigation_failed', 'page_location',
    'selector_missing', 'dashboard_retry', 'credential_step_completed',
    'microsoft_form', 'passkey_decline_enabled', 'failure_pause',
    'api_transport_retry', 'api_attempt', 'api_validated', 'run_failed', 'limit_reached',
}
ACTIONS = {
    'student_entry_clicked', 'post_sso_student_entry_clicked', 'sign_in_clicked', 'student_sso_clicked',
    'my_studium_clicked', 'timetable_link_clicked', 'portal_start',
    'schedule_fallback', 'direct_api', 'email_submit', 'password_submit', 'keep_signed_in', 'password_choice',
}
HOSTS = {
    'hvsweb.provadis-hochschule.de', 'api.provadis-hochschule.de',
    'ishgroupb2c.b2clogin.com', 'login.microsoft.com', 'login.microsoftonline.com', 'login.live.com',
}
PATHS = {
    '/common/bridge/fido', '/', '/login/student', '/student', '/student/', '/student/schedule',
    '/student/overview', '/api/v1/Student/GetEventList', '/common/oauth2/authorize',
    '/common/oauth2/v2.0/authorize', '/common/login', '/login.srf',
    '/ishgroupb2c.onmicrosoft.com/b2c_1a_hvs_signup_signin/oauth2/v2.0/authorize',
}


def safe_url(value: str) -> dict:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None, 443):
            return {'origin': 'other', 'path': '<redacted>'}
        path = parsed.path or '/'
        result = {
            'origin': 'https://' + parsed.hostname,
            'path': path if path in PATHS else '<redacted>',
            'query_redacted': bool(parsed.query), 'fragment_redacted': bool(parsed.fragment),
        }
        if path not in PATHS:
            result['route_id'] = hashlib.sha256(path.encode()).hexdigest()[:12]
        return result
    except ValueError:
        return {'origin': 'other', 'path': '<redacted>'}


class NavigationLog:
    def __init__(self, directory: Path):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.path = directory / f'navigation-{stamp}-{uuid.uuid4().hex[:8]}.jsonl'
        self.started = time.monotonic()
        self.sequence = 0
        self.last_url = None
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        self.file = os.fdopen(fd, 'w', encoding='utf-8')

    def __enter__(self):
        self('log_started')
        return self

    def __exit__(self, exc_type, _exc, _tb):
        try:
            self('log_finished', outcome='failed' if exc_type else 'completed')
        finally:
            self.file.close()

    def __call__(self, event: str, *, action=None, url=None, outcome=None,
                 status=None, matches=None, state=None, operation=None, error_kind=None):
        if operation is not None and operation not in UI_OPERATIONS:
            raise ValueError('unrecognized UI operation')
        if error_kind is not None and error_kind not in UI_ERROR_KINDS:
            raise ValueError('unrecognized UI error category')
        if state not in (None, "starting", "redirect", "student", "portal_login",
                         "b2c_microsoft", "b2c_student", "email", "password",
                         "password_choice", "kmsi", "study", "schedule", "timetable",
                         "native_passkey", "unknown"):
            raise ValueError("unrecognized authentication state")
        if event not in EVENTS or (action is not None and action not in ACTIONS):
            raise ValueError('unrecognized navigation log event/action')
        if outcome not in (None, 'failed', 'completed'):
            raise ValueError('unrecognized navigation log outcome')
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError('invalid navigation status')
        if matches is not None and (type(matches) is not int or matches < 0):
            raise ValueError('invalid selector count')
        if self.sequence >= 2000:
            return
        if self.sequence == 1999:
            event, action, url, outcome, status, matches = 'limit_reached', None, None, None, None, None
            state, operation, error_kind = None, None, None
        self.sequence += 1
        row = {
            'sequence': self.sequence, 'at': datetime.now(timezone.utc).isoformat(),
            'elapsed_ms': round((time.monotonic() - self.started) * 1000), 'event': event,
        }
        for key, value in (('action', action), ('outcome', outcome), ('status', status),
                           ('matches', matches), ('state', state), ('operation', operation),
                           ('error_kind', error_kind)):
            if value is not None:
                row[key] = value
        if url is not None:
            row['url'] = safe_url(url)
        self.file.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.file.flush()

    def observe_url(self, url: str):
        sanitized = safe_url(url)
        if sanitized != self.last_url:
            self('page_location', url=url)
            self.last_url = sanitized


def attach_navigation_log(page, trace):
    """Observe main-frame navigation only; no headers, bodies, or console errors."""
    def is_main_navigation(request):
        return request.is_navigation_request() and request.frame == page.main_frame

    def frame_navigated(frame):
        if frame == page.main_frame:
            trace('frame_navigated', url=frame.url)

    def request_started(request):
        if is_main_navigation(request):
            trace('navigation_request', url=request.url)

    def response_received(response):
        if is_main_navigation(response.request):
            trace('navigation_response', url=response.url, status=response.status)

    def request_failed(request):
        if is_main_navigation(request):
            trace('navigation_failed', url=request.url)

    page.on('framenavigated', frame_navigated)
    page.on('request', request_started)
    page.on('response', response_received)
    page.on('requestfailed', request_failed)
