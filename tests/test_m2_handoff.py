"""Use the actual M1 navigation/fetch/parser path with synthetic transport."""

import json
import unittest
from unittest.mock import Mock

import test_auth_runner as runner
from otter.models import SnapshotCandidate
from provadis_m1.browser import PortalCapture, direct_fetch
from provadis_m1.errors import AuthenticationError, InvalidResponse
from provadis_m1.validation import parse_candidate_response


class HandoffTests(unittest.TestCase):
    def invoke(self, **kwargs):
        self.harness = runner.RunnerTests()
        return self.harness.invoke([runner.plan(runner.WET, "WetLogin")],
                                   snapshot_validator=parse_candidate_response, **kwargs)

    def test_actual_browser_pipeline_delivers_private_candidate_not_summary_details(self):
        delivered = []
        result = self.invoke(on_snapshot=delivered.append)
        self.assertEqual(len(delivered), 1)
        self.assertIsInstance(delivered[0], SnapshotCandidate)
        self.assertEqual(delivered[0].event_count, 2)
        self.assertEqual(delivered[0].events[0].title, "Synthetic lecture A")
        self.assertEqual(result["event_counts"], [2])
        for forbidden in ("Synthetic lecture", "eventtitle", "example@example.invalid", "events"):
            self.assertNotIn(forbidden, json.dumps(result))
        self.harness.context.close.assert_called_once()
        self.harness.responses[0].dispose.assert_called_once()

    def test_empty_candidate_is_handed_off_without_changing_default_m1_policy(self):
        delivered = []
        result = self.invoke(body=b'{"ok":true,"count":0,"data":[]}',
                             on_snapshot=delivered.append)
        self.assertEqual(result["event_counts"], [0])
        self.assertEqual(delivered[0].events, ())

    def test_invalid_later_row_delivers_nothing_and_disposes_response(self):
        payload = json.loads(runner.VALID)
        payload["data"][1]["enddate"] = "broken"
        consumer = Mock()
        with self.assertRaises(InvalidResponse):
            self.invoke(body=json.dumps(payload).encode(), on_snapshot=consumer)
        consumer.assert_not_called()
        self.harness.context.request.get.assert_called_once()
        self.harness.responses[0].dispose.assert_called_once()
        self.harness.context.close.assert_called_once()

    def test_401_recovery_delivers_only_final_candidate(self):
        consumer = Mock()
        self.invoke(statuses=(401, 200), on_snapshot=consumer)
        self.assertEqual(self.harness.context.request.get.call_count, 2)
        consumer.assert_called_once()
        for response in self.harness.responses:
            response.dispose.assert_called_once()

    def test_separate_samples_have_separate_handoffs_even_if_identical(self):
        consumer = Mock()
        result = self.invoke(statuses=(200, 200), samples=2, on_snapshot=consumer)
        self.assertEqual(result["event_counts"], [2, 2])
        self.assertEqual(consumer.call_count, 2)

    def test_consumer_errors_never_retry_authentication_or_gets(self):
        for error in (RuntimeError("PRIVATE-CANARY"), AuthenticationError()):
            consumer = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.invoke(on_snapshot=consumer)
            consumer.assert_called_once()
            self.harness.context.request.get.assert_called_once()
            self.assertEqual(self.harness.page.starts, 1)
            self.harness.responses[0].dispose.assert_called_once()
            self.harness.context.close.assert_called_once()

    def test_transport_retry_retains_existing_budget_and_delivers_normalized_data(self):
        response = Mock(url=runner.API_URL, status=200,
                        headers={"content-type": "application/json"},
                        body=Mock(return_value=runner.VALID))
        context = Mock()
        context.request.get.side_effect = [runner.BrowserError("ECONNRESET"), response]
        capture = PortalCapture()
        result = direct_fetch(context, "Bearer SYNTHETIC", 1000, runner.BrowserError,
                              capture, validator=parse_candidate_response)
        self.assertIsInstance(result, SnapshotCandidate)
        self.assertEqual((capture.direct_attempts, capture.direct_transport_failures), (2, 1))
        for call in context.request.get.call_args_list:
            self.assertEqual(call.kwargs["max_redirects"], 0)
            self.assertEqual(call.kwargs["max_retries"], 0)
        response.dispose.assert_called_once()
