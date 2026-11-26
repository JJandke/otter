"""Pure snapshot assessment; no clock reads, event matching or database writes."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from provadis_m1.errors import InvalidResponse

from .models import SnapshotCandidate, SnapshotFingerprint
from .normalization import fingerprint_snapshot, utc_microseconds


def _instant(value: datetime) -> int:
    if not isinstance(value, datetime):
        raise ValueError("assessment_time_must_be_aware")
    try:
        return utc_microseconds(value)
    except InvalidResponse:
        raise ValueError("assessment_time_must_be_representable_and_aware") from None


@dataclass(frozen=True, slots=True)
class AssessmentPolicy:
    """Hold a future-count drop only when BOTH thresholds are exceeded/met.

    Absolute drop must be >= minimum_future_drop; relative drop must be strictly
    > future_drop_percent. Integer arithmetic makes boundary decisions exact.
    Empty/duplicate/clock protections are independent of these thresholds.
    """

    minimum_future_drop: int = 5
    future_drop_percent: int = 30
    version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if type(self.minimum_future_drop) is not int or self.minimum_future_drop < 1:
            raise ValueError("minimum_future_drop_must_be_positive_integer")
        if type(self.future_drop_percent) is not int or not 0 <= self.future_drop_percent <= 100:
            raise ValueError("future_drop_percent_must_be_integer_between_0_and_100")


@dataclass(frozen=True, slots=True)
class AcceptedBaseline:
    """Caller-supplied last ACCEPTED observation, never a held/failed fetch.

    Later storage/ingestion must enforce that provenance. This pure value does
    not grant approval or prove that a snapshot was previously persisted.
    """

    candidate: SnapshotCandidate = field(repr=False)
    received_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, SnapshotCandidate):
            raise TypeError("baseline_requires_snapshot_candidate")
        _instant(self.received_at)


@dataclass(frozen=True, slots=True)
class SnapshotAssessment:
    """Recommendation for ingestion; accepted here does not mean committed."""

    status: Literal["accepted", "suspicious"]
    reasons: tuple[str, ...]
    information: tuple[str, ...]
    fingerprint: SnapshotFingerprint = field(repr=False)
    reference_utc_us: int
    candidate_event_count: int
    candidate_future_count: int
    baseline_event_count: int | None
    baseline_future_count: int | None
    duplicate_excess_count: int
    policy: AssessmentPolicy

    def safe_diagnostics(self) -> dict:
        """Allowlisted decision evidence; no event details, digest or raw errors."""
        return {
            "status": self.status, "reasons": list(self.reasons),
            "information": list(self.information), "reference_utc_us": self.reference_utc_us,
            "candidate_event_count": self.candidate_event_count,
            "candidate_future_count": self.candidate_future_count,
            "baseline_event_count": self.baseline_event_count,
            "baseline_future_count": self.baseline_future_count,
            "duplicate_excess_count": self.duplicate_excess_count,
            "normalization_version": self.fingerprint.normalization_version,
            "content_format_version": self.fingerprint.format_version,
            "policy_version": self.policy.version,
            "minimum_future_drop": self.policy.minimum_future_drop,
            "future_drop_percent": self.policy.future_drop_percent,
        }


def assess_snapshot(
    candidate: SnapshotCandidate, *, received_at: datetime,
    baseline: AcceptedBaseline | None = None, policy: AssessmentPolicy = AssessmentPolicy(),
) -> SnapshotAssessment:
    """Assess structurally validated data at one explicit common reference time.

    Future counts include in-progress lectures (end > received_at). Historical
    rows remain in the candidate/fingerprint; they are excluded only from this
    anomaly metric. Large growth alone is never suspicious. No source scope or
    semantic completeness guarantee can be established from counts alone.
    """
    if not isinstance(candidate, SnapshotCandidate):
        raise TypeError("assessment_requires_snapshot_candidate")
    if baseline is not None and not isinstance(baseline, AcceptedBaseline):
        raise TypeError("assessment_requires_accepted_baseline")
    if not isinstance(policy, AssessmentPolicy):
        raise TypeError("assessment_requires_policy")
    reference = _instant(received_at)
    candidate_future = sum(event.end_utc_us > reference for event in candidate.events)
    duplicate_excess = sum(count - 1 for count in Counter(candidate.events).values())
    reasons = []
    information = []
    baseline_count = None
    baseline_future = None
    if not candidate.events:
        reasons.append("empty_timetable")
    if duplicate_excess:
        reasons.append("duplicate_events")
    if baseline is not None:
        if reference < _instant(baseline.received_at):
            reasons.append("observation_time_regressed")
        baseline_count = baseline.candidate.event_count
        baseline_future = sum(event.end_utc_us > reference for event in baseline.candidate.events)
        drop = baseline_future - candidate_future
        if (baseline_future > 0 and drop >= policy.minimum_future_drop
                and drop * 100 > baseline_future * policy.future_drop_percent):
            reasons.append("unexpected_future_decrease")
        if candidate.event_count > baseline_count:
            information.append("total_event_count_increased")
        if candidate_future > baseline_future:
            information.append("future_event_count_increased")
    return SnapshotAssessment(
        status="suspicious" if reasons else "accepted", reasons=tuple(reasons),
        information=tuple(information), fingerprint=fingerprint_snapshot(candidate),
        reference_utc_us=reference, candidate_event_count=candidate.event_count,
        candidate_future_count=candidate_future, baseline_event_count=baseline_count,
        baseline_future_count=baseline_future, duplicate_excess_count=duplicate_excess,
        policy=policy,
    )
