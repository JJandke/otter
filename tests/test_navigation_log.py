import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from provadis_m1.navigation_log import NavigationLog, attach_navigation_log, safe_url

PORTAL = 'https://hvsweb.provadis-hochschule.de'


class NavigationLogTests(unittest.TestCase):
    def test_dashboard_retry_has_only_fixed_diagnostic_fields(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            log('dashboard_retry')
            row = json.loads(log.path.read_text().splitlines()[-1])
            self.assertEqual(row['event'], 'dashboard_retry')
            self.assertEqual(set(row), {'sequence', 'at', 'elapsed_ms', 'event'})

    def test_url_redaction_covers_callbacks_credentials_and_unknown_routes(self):
        urls = [
            PORTAL + '/?code=SECRET&email=person@example.invalid#id_token=SECRET',
            'https://user:SECRET@hvsweb.provadis-hochschule.de/student/schedule',
            PORTAL + '/student/person@example.invalid/SECRET',
            'https://ishgroupb2c.b2clogin.com/private/SECRET?state=SECRET',
            'https://SECRET.example.invalid/SECRET',
            'https://hvsweb.provadis-hochschule.de:8443/SECRET',
            'about:blank', 'https://[broken/SECRET',
        ]
        for url in urls:
            with self.subTest(url=url):
                serialized = json.dumps(safe_url(url))
                self.assertNotIn('SECRET', serialized)
                self.assertNotIn('person@example.invalid', serialized)
                self.assertNotIn('user:', serialized)
        self.assertEqual(safe_url(PORTAL + '/student/schedule')['path'], '/student/schedule')
        self.assertTrue(safe_url(PORTAL + '/#code=SECRET')['fragment_redacted'])

    def test_file_is_private_unique_flushed_and_closed_after_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                with NavigationLog(Path(temp)) as log:
                    log('click_attempt', action='sign_in_clicked')
                    log('goto_completed', action='portal_start', url=PORTAL + '/#code=SECRET')
                    self.assertEqual(len(log.path.read_text().splitlines()), 3)
                    raise RuntimeError('SECRET must never be logged')
            self.assertTrue(log.file.closed)
            self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)
            rows = [json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertEqual(rows[-1]['outcome'], 'failed')
            self.assertEqual([r['sequence'] for r in rows], list(range(1, len(rows) + 1)))
            self.assertNotIn('SECRET', log.path.read_text())
            with NavigationLog(Path(temp)) as other:
                self.assertNotEqual(log.path, other.path)

    def test_unknown_fields_and_actions_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            for kwargs in ({'action': 'SECRET'}, {'outcome': 'SECRET'}, {'status': 'SECRET'},
                           {'matches': -1}):
                with self.assertRaises(ValueError):
                    log('click_attempt', **kwargs)
            with self.assertRaises(ValueError):
                log('SECRET')
            with self.assertRaises(TypeError):
                log('click_attempt', headers={'Authorization': 'SECRET'})
            self.assertNotIn('SECRET', log.path.read_text())

    def test_repeated_locations_deduplicate_even_when_token_values_change(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            log.observe_url(PORTAL + '/#code=ONE')
            log.observe_url(PORTAL + '/#code=TWO')
            log.observe_url(PORTAL + '/student/schedule')
            rows = [json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertEqual(sum(r['event'] == 'page_location' for r in rows), 2)

    def test_only_main_frame_navigation_is_recorded(self):
        page = Mock()
        trace = Mock()
        attach_navigation_log(page, trace)
        callbacks = {c.args[0]: c.args[1] for c in page.on.call_args_list}
        request = Mock(url=PORTAL + '/', frame=page.main_frame)
        request.is_navigation_request.return_value = False
        callbacks['request'](request)
        trace.assert_not_called()
        request.is_navigation_request.return_value = True
        request.frame = object()
        callbacks['request'](request)
        trace.assert_not_called()
        request.frame = page.main_frame
        callbacks['request'](request)
        trace.assert_called_once_with('navigation_request', url=PORTAL + '/')
        request.all_headers.assert_not_called()
        callbacks['response'](Mock(request=request, url=PORTAL + '/', status=302))
        self.assertEqual(trace.call_args.kwargs['status'], 302)

    def test_auth_states_are_allowlisted_and_logged_without_page_content(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            log("auth_state", state="password")
            log("auth_action", state="password_choice")
            with self.assertRaises(ValueError):
                log("auth_state", state="SECRET")
            self.assertNotIn("SECRET", log.path.read_text())
            self.assertEqual(json.loads(log.path.read_text().splitlines()[-1])["state"],
                             "password_choice")

    def test_log_has_a_fixed_event_limit(self):
        with tempfile.TemporaryDirectory() as temp, NavigationLog(Path(temp)) as log:
            for _ in range(2100):
                log('browser_started')
            rows = log.path.read_text().splitlines()
            self.assertEqual(len(rows), 2000)
            self.assertEqual(json.loads(rows[-1])['event'], 'limit_reached')
