"""Explicit import IDs must never manufacture live retrieval evidence."""

import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from otter._store_files import StorageError
from otter.ingestion import IngestionError, ObservationContext, PendingObservationError

import test_m2_ingestion as fixtures
import test_m2_assessment as samples


class ImportLifecycleTests(unittest.TestCase):
    setUp = fixtures.IngestionTests.setUp
    ingest = fixtures.IngestionTests.ingest

    def test_live_id_cannot_be_reused_as_import_before_reading_file(self):
        live = self.ingest()
        reader = Mock(return_value=samples.snapshot(future=10))
        with self.assertRaisesRegex(IngestionError, "observation_id_conflict"):
            self.service.import_candidate(live.observation_uuid, reader)
        reader.assert_not_called()

    def test_uncaptured_abandoned_import_is_interrupted_not_reread_under_old_id(self):
        observation_id = str(uuid4())
        self.service._start(ObservationContext(observation_id, "import", fixtures.NOW))
        reader = Mock()
        with self.assertRaisesRegex(IngestionError, "recorded_observation_has_no_receipt"):
            self.service.import_candidate(observation_id, reader)
        reader.assert_not_called()
        self.assertEqual(self.service.result(observation_id).status, "interrupted")

    def test_pending_import_can_retry_exact_contents_without_creating_an_observation(self):
        observation_id = str(uuid4())
        reader = Mock(return_value=samples.snapshot(future=10))
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(PendingObservationError):
                self.service.import_candidate(observation_id, reader)
        result = self.service.import_candidate(observation_id, reader)
        self.assertEqual(result.status, "accepted")
        self.assertFalse(result.eligible_for_sync)
        self.assertEqual(self.store.status()["attempt_count"], 1)

    def test_terminal_interrupted_import_stays_interrupted_on_matching_replay(self):
        observation_id = str(uuid4())
        reader = Mock(return_value=samples.snapshot(future=10))
        with patch.object(self.store, "put_content", side_effect=StorageError("storage_sql_failed")):
            with self.assertRaises(PendingObservationError):
                self.service.import_candidate(observation_id, reader)
        self.service.recover_interrupted()
        self.assertEqual(self.service.import_candidate(observation_id, reader).status, "interrupted")
        self.assertIsNone(self.store.last_accepted())
