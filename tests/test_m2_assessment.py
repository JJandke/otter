"""Synthetic semester progression, boundary and privacy tests; no live services."""

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from otter.assessment import AcceptedBaseline, AssessmentPolicy, assess_snapshot
from otter.models import SnapshotCandidate
from otter.normalization import fingerprint_snapshot, normalize_event
from provadis_m1.errors import InvalidResponse
from provadis_m1.validation import parse_candidate_response

REFERENCE = datetime(2027, 6, 1, 12, tzinfo=timezone.utc)


def event(index, *, past=False):
    start = REFERENCE + timedelta(hours=(-index - 3) if past else index + 1)
    return normalize_event(title=f"Synthetic lecture {index}", start=start,
                           end=start + timedelta(hours=1), building=None, room=None)


def snapshot(future=0, past=0):
    return SnapshotCandidate(events=tuple(
        [event(i) for i in range(future)] + [event(i, past=True) for i in range(past)]
    ))


def baseline(candidate):
    return AcceptedBaseline(candidate, REFERENCE - timedelta(days=30))


class AssessmentTests(unittest.TestCase):
    def assess(self, candidate, previous=None, **kwargs):
        return assess_snapshot(candidate, received_at=REFERENCE,
                               baseline=baseline(previous) if previous is not None else None,
                               **kwargs)

    def test_first_valid_nonempty_snapshot_is_accepted(self):
        result = self.assess(snapshot(future=10))
        self.assertEqual((result.status, result.reasons), ("accepted", ()))
        self.assertIsNone(result.baseline_future_count)
        self.assertEqual(result.candidate_future_count, 10)

    def test_old_only_snapshot_is_accepted_without_a_date_cutoff(self):
        old = normalize_event(title="Old source observation",
                              start=datetime(2023, 3, 3, 8, tzinfo=timezone.utc),
                              end=datetime(2023, 3, 3, 13, tzinfo=timezone.utc),
                              building=None, room=None)
        candidate = SnapshotCandidate(events=(old,))
        result = self.assess(candidate)
        self.assertEqual(result.status, "accepted")
        self.assertEqual((result.candidate_event_count, result.candidate_future_count), (1, 0))
        self.assertEqual(result.fingerprint.event_count, 1)
        self.assertEqual(candidate.events, (old,))

    def test_normal_semester_progression_removes_completed_rows(self):
        previous = snapshot(future=10, past=60)
        candidate = snapshot(future=10)
        result = self.assess(candidate, previous)
        self.assertEqual(result.status, "accepted")
        self.assertEqual((result.baseline_event_count, result.candidate_event_count), (70, 10))
        self.assertEqual((result.baseline_future_count, result.candidate_future_count), (10, 10))
        self.assertEqual(previous.event_count, 70)

    def test_completed_rows_retained_by_source_are_not_an_anomaly(self):
        result = self.assess(snapshot(future=10, past=60), snapshot(future=10, past=60))
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.candidate_future_count, 10)

    def test_sudden_new_semester_publication_is_accepted(self):
        result = self.assess(snapshot(future=260), snapshot(future=10))
        self.assertEqual((result.status, result.reasons), ("accepted", ()))
        self.assertEqual(result.information,
                         ("total_event_count_increased", "future_event_count_increased"))

    def test_new_semester_can_grow_from_zero_unfinished_events(self):
        result = self.assess(snapshot(future=250), snapshot(past=4))
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.baseline_future_count, 0)
        self.assertIn("future_event_count_increased", result.information)

    def test_historical_growth_alone_is_informational(self):
        result = self.assess(snapshot(future=10, past=500), snapshot(future=10))
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.information, ("total_event_count_increased",))

    def test_growth_does_not_hide_a_real_future_count_drop(self):
        result = self.assess(snapshot(future=5, past=100), snapshot(future=20))
        self.assertEqual(result.status, "suspicious")
        self.assertIn("unexpected_future_decrease", result.reasons)
        self.assertIn("total_event_count_increased", result.information)

    def test_decrease_requires_both_absolute_and_relative_conditions(self):
        for before, after, status in ((10, 6, "accepted"), (10, 5, "suspicious"),
                                       (20, 14, "accepted"), (20, 13, "suspicious")):
            with self.subTest(before=before, after=after):
                self.assertEqual(self.assess(snapshot(future=after),
                                             snapshot(future=before)).status, status)

    def test_configurable_decrease_thresholds_and_exact_boundaries(self):
        policy = AssessmentPolicy(minimum_future_drop=2, future_drop_percent=50)
        self.assertEqual(self.assess(snapshot(future=2), snapshot(future=4),
                                    policy=policy).status, "accepted")  # exactly 50%
        result = self.assess(snapshot(future=1), snapshot(future=4), policy=policy)
        self.assertEqual(result.status, "suspicious")
        self.assertEqual(result.safe_diagnostics()["minimum_future_drop"], 2)
        self.assertEqual(result.safe_diagnostics()["future_drop_percent"], 50)
        self.assertEqual(result.safe_diagnostics()["policy_version"], 1)

    def test_zero_and_hundred_percent_policy_boundaries(self):
        self.assertEqual(self.assess(snapshot(future=3), snapshot(future=4),
                                    policy=AssessmentPolicy(1, 0)).status, "suspicious")
        self.assertEqual(self.assess(snapshot(past=1), snapshot(future=10),
                                    policy=AssessmentPolicy(1, 100)).status, "accepted")
        self.assertEqual(self.assess(snapshot(), snapshot(future=10),
                                    policy=AssessmentPolicy(1, 100)).status, "suspicious")

    def test_empty_is_always_suspicious_including_repeated_empty(self):
        for previous in (None, snapshot(), snapshot(past=10), snapshot(future=10)):
            with self.subTest(previous=previous):
                result = self.assess(snapshot(), previous)
                self.assertEqual(result.status, "suspicious")
                self.assertIn("empty_timetable", result.reasons)

    def test_exact_duplicates_are_preserved_and_held(self):
        candidate = SnapshotCandidate(events=(event(0), event(0), event(0), event(1), event(1)))
        result = self.assess(candidate)
        self.assertEqual(result.reasons, ("duplicate_events",))
        self.assertEqual(result.duplicate_excess_count, 3)
        self.assertEqual(result.candidate_event_count, 5)
        self.assertEqual(result.candidate_future_count, 5)
        self.assertEqual(candidate.event_count, 5)

    def test_same_title_separate_lectures_and_room_changes_are_accepted(self):
        rows = [normalize_event(title="Repeated title", start=REFERENCE + timedelta(days=i + 1),
                                end=REFERENCE + timedelta(days=i + 1, hours=1),
                                building=None, room=None) for i in range(10)]
        previous = SnapshotCandidate(events=tuple(rows))
        rows[0] = replace(rows[0], building="Building", room="Room")
        result = self.assess(SnapshotCandidate(events=tuple(rows)), previous)
        self.assertEqual((result.status, result.duplicate_excess_count), ("accepted", 0))

    def test_no_location_is_valid_and_never_filled(self):
        candidate = snapshot(future=1)
        self.assertEqual(self.assess(candidate).status, "accepted")
        self.assertIsNone(candidate.events[0].building)
        self.assertIsNone(candidate.events[0].room)

    def test_common_reference_excludes_exact_end_and_includes_in_progress(self):
        finished = normalize_event(title="Finished", start=REFERENCE - timedelta(hours=1),
                                   end=REFERENCE, building=None, room=None)
        ongoing = normalize_event(title="Ongoing", start=REFERENCE - timedelta(minutes=30),
                                  end=REFERENCE + timedelta(minutes=30), building=None, room=None)
        previous = SnapshotCandidate(events=(finished, ongoing))
        result = self.assess(SnapshotCandidate(events=(ongoing,)), previous)
        self.assertEqual(result.status, "accepted")
        self.assertEqual((result.baseline_future_count, result.candidate_future_count), (1, 1))

    def test_clock_regression_is_held_but_equal_times_are_allowed(self):
        candidate = snapshot(future=2)
        for difference, status in ((-1, "accepted"), (0, "accepted"), (1, "suspicious")):
            previous = AcceptedBaseline(candidate, REFERENCE + timedelta(microseconds=difference))
            result = assess_snapshot(candidate, received_at=REFERENCE, baseline=previous)
            with self.subTest(difference=difference):
                self.assertEqual(result.status, status)
                self.assertEqual("observation_time_regressed" in result.reasons, difference > 0)

    def test_different_timezones_for_same_reference_produce_same_decision(self):
        candidate = snapshot(future=2)
        previous = baseline(candidate)
        utc = assess_snapshot(candidate, received_at=REFERENCE, baseline=previous)
        offset = assess_snapshot(candidate, baseline=previous,
                                 received_at=REFERENCE.astimezone(timezone(timedelta(hours=-5))))
        self.assertEqual(utc, offset)

    def test_held_and_invalid_candidates_do_not_mutate_baseline(self):
        previous = baseline(snapshot(future=10))
        before = fingerprint_snapshot(previous.candidate)
        held = assess_snapshot(snapshot(), received_at=REFERENCE, baseline=previous)
        self.assertEqual(held.status, "suspicious")
        with self.assertRaises(InvalidResponse):
            parse_candidate_response(200, "application/json", b'{"ok":true,"count":1,"data":[]}')
        self.assertEqual(fingerprint_snapshot(previous.candidate), before)
        self.assertEqual(assess_snapshot(previous.candidate, received_at=REFERENCE,
                                         baseline=previous).status, "accepted")

    def test_every_applicable_reason_is_retained_in_fixed_order(self):
        candidate = SnapshotCandidate(events=(event(0), event(0)))
        previous = AcceptedBaseline(snapshot(future=10), REFERENCE + timedelta(seconds=1))
        result = assess_snapshot(candidate, received_at=REFERENCE, baseline=previous)
        self.assertEqual(result.reasons, ("duplicate_events", "observation_time_regressed",
                                          "unexpected_future_decrease"))

    def test_decisions_are_immutable_repeatable_and_safe_to_report(self):
        candidate = SnapshotCandidate(events=(normalize_event(
            title="PRIVATE-TIMETABLE-CANARY", start=REFERENCE, end=REFERENCE + timedelta(hours=1),
            building="PRIVATE-BUILDING-CANARY", room="PRIVATE-ROOM-CANARY"),))
        result = self.assess(candidate)
        self.assertEqual(result, self.assess(candidate))
        with self.assertRaises(FrozenInstanceError):
            result.status = "suspicious"
        with self.assertRaises(FrozenInstanceError):
            result.policy.minimum_future_drop = 1
        output = json.dumps(result.safe_diagnostics()) + repr(result)
        self.assertNotIn("CANARY", output)
        self.assertNotIn(result.fingerprint.sha256, output)
        self.assertEqual(result.safe_diagnostics()["candidate_event_count"], 1)

    def test_local_input_errors_are_fixed_and_not_assessments(self):
        candidate = snapshot(future=1)
        for value in (None, "PRIVATE-CANARY", datetime(2027, 1, 1)):
            with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                assess_snapshot(candidate, received_at=value)
            self.assertNotIn("PRIVATE-CANARY", str(raised.exception))
        with self.assertRaises(TypeError):
            assess_snapshot(None, received_at=REFERENCE)
        with self.assertRaises(TypeError):
            assess_snapshot(candidate, received_at=REFERENCE, baseline=candidate)
        with self.assertRaises(TypeError):
            assess_snapshot(candidate, received_at=REFERENCE, policy={})
        with self.assertRaises(TypeError):
            AcceptedBaseline(None, REFERENCE)
        with self.assertRaises(ValueError):
            AcceptedBaseline(candidate, datetime(2027, 1, 1))

    def test_policy_rejects_invalid_configuration_values(self):
        for value in (True, False, 0, -1, 2.5, "5", None):
            with self.subTest(minimum=value), self.assertRaises(ValueError):
                AssessmentPolicy(minimum_future_drop=value)
        for value in (True, False, -1, 101, 30.0, float("nan"), "30", None):
            with self.subTest(percent=value), self.assertRaises(ValueError):
                AssessmentPolicy(future_drop_percent=value)
