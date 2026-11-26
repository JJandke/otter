"""Atomic observation lifecycle; providers are injected, never authenticated here."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from provadis_m1.errors import (
    AuthenticationError, BootstrapError, ConfigurationError, InvalidResponse,
    NetworkError, ServiceError, SuspiciousResponse,
)

from ._store_files import StorageError
from .assessment import AssessmentPolicy, assess_snapshot
from .models import SnapshotCandidate
from .normalization import EPOCH, canonical_snapshot_bytes, fingerprint_snapshot, utc_microseconds
from .storage import Store, _validate_candidate

ASSESSMENT_REASONS = frozenset({"empty_timetable", "duplicate_events",
                              "observation_time_regressed", "unexpected_future_decrease"})
INFORMATION = frozenset({"total_event_count_increased", "future_event_count_increased"})
FAILURE_REASONS = frozenset({
    "duplicate_json_key", "non_finite_json_number", "invalid_event_date",
    "event_date_missing_offset", "api_access_denied", "unexpected_api_redirect",
    "api_temporarily_unavailable", "unexpected_http_status", "expected_json_content_type",
    "invalid_response_size", "malformed_json", "expected_envelope_object", "missing_envelope_fields",
    "api_reported_failure", "invalid_message_type", "invalid_data_or_count_type", "count_mismatch",
    "invalid_event_object", "missing_event_fields", "empty_event_title", "invalid_event_text_field",
    "invalid_event_interval", "event_offset_requires_whole_seconds", "invalid_content_length",
    "unexpected_response_url", "authentication_required", "network_request_failed",
    "direct_api_timeout", "direct_api_transport_failed", "response_read_failed",
    "portal_did_not_issue_timetable_bearer", "provider_failed", "provider_contract_failed",
    "operator_interrupted", "abandoned_observation", "observation_write_failed", "fetch_failed",
    "input_file_unavailable", "candidate_handoff_invalid",
    "playwright_not_installed", "browser_launch_failed", "profile_already_in_use", "unsafe_state_parent",
    "credentials_file_unreadable_or_invalid", "credentials_file_must_be_owner_only_regular_file",
    "credentials_file_must_be_outside_project_and_old_application",
})
REVIEW_REASONS = {"confirmed_empty": "empty_timetable", "confirmed_duplicates": "duplicate_events",
                  "confirmed_change": "unexpected_future_decrease"}
MAX_REVIEW_AGE = timedelta(hours=2)


class IngestionError(Exception):
    """Fixed local errors; never include provider text, payloads or SQL."""


def _instant(value: datetime) -> int:
    if not isinstance(value, datetime):
        raise IngestionError("invalid_observation_time")
    try:
        return utc_microseconds(value)
    except InvalidResponse:
        raise IngestionError("invalid_observation_time") from None


def _uuid(value: str) -> None:
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise IngestionError("invalid_observation_uuid")


@dataclass(frozen=True, slots=True)
class ObservationContext:
    observation_uuid: str
    origin: str
    started_at: datetime

    def __post_init__(self):
        _uuid(self.observation_uuid)
        if self.origin not in ("live", "import"):
            raise IngestionError("invalid_observation_origin")
        _instant(self.started_at)


@dataclass(frozen=True, slots=True, repr=False)
class CapturedSnapshot:
    """A previously retrieved normalized candidate and its ORIGINAL receipt time."""

    candidate: SnapshotCandidate
    received_at: datetime

    def __post_init__(self):
        if not isinstance(self.candidate, SnapshotCandidate):
            raise IngestionError("capture_requires_snapshot_candidate")
        _instant(self.received_at)


class PendingObservationError(StorageError):
    """Retry persistence using these private objects; never rerun the provider.

    Exception text/repr contains only a fixed code. Attributes deliberately keep
    the capture in memory for the caller; do not serialize the exception dict.
    """

    def __init__(self, context: ObservationContext, capture: CapturedSnapshot):
        self.context, self.capture = context, capture
        super().__init__("observation_write_pending")


def _codes(value: str, allowed: frozenset) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        raise IngestionError("invalid_assessment_record") from None
    if not isinstance(parsed, list) or any(not isinstance(v, str) or v not in allowed for v in parsed):
        raise IngestionError("invalid_assessment_record")
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class ObservationResult:
    observation_uuid: str
    attempt_no: int
    origin: str
    status: str
    content_id: int | None
    reason: str | None
    category: str | None
    reasons: tuple[str, ...]
    information: tuple[str, ...]
    eligible_for_sync: bool
    reviewed: bool

    def safe_summary(self) -> dict:
        return {"observation_uuid": self.observation_uuid, "attempt_no": self.attempt_no,
                "origin": self.origin, "status": self.status, "content_id": self.content_id,
                "reason": self.reason, "category": self.category, "reasons": list(self.reasons),
                "information": list(self.information), "eligible_for_sync": self.eligible_for_sync,
                "reviewed": self.reviewed}


class IngestionService:
    def __init__(self, store: Store, *, clock: Callable[[], datetime] | None = None,
                 policy: AssessmentPolicy = AssessmentPolicy()):
        if not isinstance(policy, AssessmentPolicy):
            raise IngestionError("invalid_ingestion_policy")
        self.store, self.policy = store, policy
        self.clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def _now(self) -> datetime:
        value = self.clock()
        _instant(value)
        return value

    def result(self, observation_uuid: str) -> ObservationResult:
        _uuid(observation_uuid)
        row = self.store.attempt(observation_uuid)
        if row is None:
            raise IngestionError("observation_not_found")
        reason = row["reason_code"]
        if reason is not None and reason not in FAILURE_REASONS | ASSESSMENT_REASONS:
            reason = "unclassified_observation_failure"
        return ObservationResult(
            observation_uuid, row["attempt_no"], row["origin"], row["status"], row["content_id"], reason,
            row["error_category"],
            _codes(row["assessment_reasons"], ASSESSMENT_REASONS),
            _codes(row["assessment_information"], INFORMATION),
            row["status"] == "accepted" and row["origin"] == "live" and row["capture_digest"] is not None,
            row["review_method"] == "operator",
        )

    def _start(self, context: ObservationContext) -> None:
        with self.store.transaction() as db:
            number = self.store.begin_attempt(context.observation_uuid, origin=context.origin,
                                              started_at=context.started_at)
            db.execute("""UPDATE fetch_attempt SET policy_version=?,minimum_future_drop=?,
                future_drop_percent=? WHERE attempt_no=?""",
                       (self.policy.version, self.policy.minimum_future_drop,
                        self.policy.future_drop_percent, number))

    def recover_interrupted(self) -> int:
        """Only while holding the store lock: no other conforming writer is active."""
        with self.store.exclusive(), self.store.transaction() as db:
            return db.execute("""UPDATE fetch_attempt SET status='interrupted',finished_at_utc_us=?,
                error_category='interrupted',reason_code='abandoned_observation' WHERE status='started'""",
                              (_instant(self._now()),)).rowcount

    @staticmethod
    def _match_context(row: dict, context: ObservationContext) -> None:
        if row["origin"] != context.origin or row["started_at_utc_us"] != _instant(context.started_at):
            raise IngestionError("observation_id_conflict")

    def _match_capture(self, row: dict, capture: CapturedSnapshot) -> None:
        fingerprint = fingerprint_snapshot(capture.candidate)
        expected = (fingerprint.sha256, fingerprint.normalization_version, fingerprint.format_version,
                    fingerprint.event_count, _instant(capture.received_at))
        actual = tuple(row[key] for key in ("capture_digest", "capture_normalization_version",
                                           "capture_format_version", "capture_event_count",
                                           "capture_received_at_utc_us"))
        if actual != expected:
            raise IngestionError("observation_id_conflict")
        if row["content_id"] is not None and canonical_snapshot_bytes(
            self.store.load_content(row["content_id"])
        ) != canonical_snapshot_bytes(capture.candidate):
            raise IngestionError("observation_id_conflict")

    @staticmethod
    def _stored_policy(row: dict) -> AssessmentPolicy:
        if row["policy_version"] != 1:
            raise IngestionError("unsupported_observation_policy")
        return AssessmentPolicy(row["minimum_future_drop"], row["future_drop_percent"])

    def _latest(self, row: dict) -> None:
        if row["attempt_no"] != self.store.latest_attempt_no():
            raise IngestionError("observation_is_not_latest")

    def _note_write_failure(self, observation_uuid: str) -> None:
        # Best effort only. Keep the receipt retryable, never label it accepted.
        try:
            with self.store.transaction() as db:
                db.execute("""UPDATE fetch_attempt SET error_category='storage',
                    reason_code='observation_write_failed' WHERE observation_uuid=? AND status='started'""",
                           (observation_uuid,))
        except Exception:
            pass  # A busy/broken DB may only retain its earlier committed start.

    def ingest_captured(self, context: ObservationContext, capture: CapturedSnapshot) -> ObservationResult:
        """Retry/import an existing receipt; never obtain fresh data under an old UUID."""
        if not isinstance(context, ObservationContext) or not isinstance(capture, CapturedSnapshot):
            raise IngestionError("invalid_captured_observation")
        _validate_candidate(capture.candidate)
        with self.store.exclusive():
            row = self.store.attempt(context.observation_uuid)
            if row is None:
                self.recover_interrupted()
                self._start(context)
                row = self.store.attempt(context.observation_uuid)
            self._match_context(row, context)
            if row["capture_digest"] is not None:
                self._match_capture(row, capture)
            elif row["status"] != "started":
                raise IngestionError("recorded_observation_has_no_receipt")
            if row["status"] != "started":
                return self.result(context.observation_uuid)
            self._latest(row)
            self._stored_policy(row)
            # Durable retry anchor, separate from content/pointer finalization.
            if row["capture_digest"] is None:
                fingerprint = fingerprint_snapshot(capture.candidate)
                with self.store.transaction() as db:
                    db.execute("""UPDATE fetch_attempt SET capture_digest=?,capture_normalization_version=?,
                        capture_format_version=?,capture_event_count=?,capture_received_at_utc_us=?
                        WHERE observation_uuid=? AND status='started'""",
                               (fingerprint.sha256, fingerprint.normalization_version, fingerprint.format_version,
                                fingerprint.event_count, _instant(capture.received_at), context.observation_uuid))
            try:
                with self.store.transaction() as db:
                    row = self.store.attempt(context.observation_uuid)
                    self._latest(row)
                    self._match_capture(row, capture)
                    previous = self.store.last_accepted()
                    assessment = assess_snapshot(capture.candidate, received_at=capture.received_at,
                                                 baseline=self.store.accepted_baseline(),
                                                 policy=self._stored_policy(row))
                    content = self.store.put_content(capture.candidate, created_at=self._now())
                    now = _instant(self._now())
                    accepted = assessment.status == "accepted"
                    db.execute("""UPDATE fetch_attempt SET status=?,received_at_utc_us=?,validated_at_utc_us=?,
                        finished_at_utc_us=?,accepted_at_utc_us=?,http_status=?,reported_count=?,content_id=?,
                        baseline_attempt_no=?,error_category=?,reason_code=?,assessment_reasons=?,assessment_information=?
                        WHERE observation_uuid=? AND status='started'""",
                               (assessment.status, _instant(capture.received_at), now, now, now if accepted else None,
                                200 if context.origin == "live" else None, capture.candidate.event_count, content,
                                previous["attempt_no"] if previous else None,
                                None if accepted else "suspicious_response",
                                assessment.reasons[0] if assessment.reasons else None,
                                json.dumps(assessment.reasons), json.dumps(assessment.information), context.observation_uuid))
                    if accepted:
                        db.execute("UPDATE store_state SET last_accepted_attempt_no=?", (row["attempt_no"],))
            except Exception as error:
                self._note_write_failure(context.observation_uuid)
                if isinstance(error, (StorageError, IngestionError)):
                    raise
                raise IngestionError("observation_processing_failed") from None
            return self.result(context.observation_uuid)

    def _provider_failure(self, context: ObservationContext, error: BaseException) -> ObservationResult:
        category = "configuration"
        status = "failed"
        reason = "provider_failed"
        for kind, name in ((InvalidResponse, "invalid_response"), (AuthenticationError, "authentication"),
                           (NetworkError, "network"), (ServiceError, "service"), (BootstrapError, "bootstrap"),
                           (ConfigurationError, "configuration"), (SuspiciousResponse, "suspicious_response")):
            if isinstance(error, kind):
                category = name
                status = "invalid" if isinstance(error, InvalidResponse) else "failed"
                reason = error.reason if isinstance(error.reason, str) and error.reason in FAILURE_REASONS else "fetch_failed"
                break
        if not isinstance(error, Exception):
            category, status, reason = "interrupted", "interrupted", "operator_interrupted"
        with self.store.transaction() as db:
            db.execute("""UPDATE fetch_attempt SET status=?,finished_at_utc_us=?,error_category=?,reason_code=?
                WHERE observation_uuid=? AND status='started'""",
                       (status, _instant(self._now()), category, reason, context.observation_uuid))
        return self.result(context.observation_uuid)

    def fetch(self, provider: Callable[[], CapturedSnapshot], *, origin: str = "live") -> ObservationResult:
        """Exactly one provider invocation per NEW UUID, no SQL transaction during I/O."""
        context = ObservationContext(str(uuid4()), origin, self._now())
        with self.store.exclusive():
            self.recover_interrupted()
            self._start(context)
            try:
                capture = provider()
                if not isinstance(capture, CapturedSnapshot):
                    raise ConfigurationError("provider_contract_failed")
                _validate_candidate(capture.candidate)
            except BaseException as error:
                result = self._provider_failure(context, error)
                if not isinstance(error, Exception):
                    raise KeyboardInterrupt() from None
                return result
            try:
                return self.ingest_captured(context, capture)
            except (StorageError, IngestionError):
                raise PendingObservationError(context, capture) from None

    def import_candidate(self, observation_uuid: str,
                         reader: Callable[[], SnapshotCandidate]) -> ObservationResult:
        """Finite file input under a stable ID, always with import origin.

        Replay verifies new contents against the original receipt. Failed/invalid
        imports need a new ID because no original payload was retained.
        """
        _uuid(observation_uuid)
        with self.store.exclusive():
            row = self.store.attempt(observation_uuid)
            if row is not None:
                if row["origin"] != "import":
                    raise IngestionError("observation_id_conflict")
                if row["capture_digest"] is None:
                    if row["status"] == "started":
                        self.recover_interrupted()
                    raise IngestionError("recorded_observation_has_no_receipt")
                context = ObservationContext(observation_uuid, "import",
                                             EPOCH + timedelta(microseconds=row["started_at_utc_us"]))
                received = EPOCH + timedelta(microseconds=row["capture_received_at_utc_us"])
                candidate = reader()
                _validate_candidate(candidate)
            else:
                self.recover_interrupted()
                context = ObservationContext(observation_uuid, "import", self._now())
                self._start(context)
                try:
                    candidate = reader()
                    _validate_candidate(candidate)
                except BaseException as error:
                    result = self._provider_failure(context, error)
                    if not isinstance(error, Exception):
                        raise KeyboardInterrupt() from None
                    return result
                received = self._now()
            capture = CapturedSnapshot(candidate, received)
            try:
                return self.ingest_captured(context, capture)
            except StorageError:
                raise PendingObservationError(context, capture) from None

    def approve(self, observation_uuid: str, *, expected_baseline_attempt_no: int | None,
                reason: str) -> ObservationResult:
        """Approve one latest, recent candidate; no new observation or fetch is created."""
        _uuid(observation_uuid)
        if reason not in REVIEW_REASONS or (expected_baseline_attempt_no is not None and
                (type(expected_baseline_attempt_no) is not int or expected_baseline_attempt_no < 1)):
            raise IngestionError("invalid_review_context")
        with self.store.exclusive(), self.store.transaction() as db:
            row = self.store.attempt(observation_uuid)
            if row is None:
                raise IngestionError("observation_not_found")
            if row["baseline_attempt_no"] != expected_baseline_attempt_no:
                raise IngestionError("review_baseline_changed")
            if row["status"] == "accepted" and row["review_method"] == "operator":
                if row["review_reason_code"] != reason:
                    raise IngestionError("review_id_conflict")
                return self.result(observation_uuid)
            if row["status"] != "suspicious" or row["capture_digest"] is None:
                raise IngestionError("observation_not_reviewable")
            self._latest(row)
            previous = self.store.last_accepted()
            current = previous["attempt_no"] if previous else None
            if current != expected_baseline_attempt_no:
                raise IngestionError("review_baseline_changed")
            now = self._now()
            age = _instant(now) - row["capture_received_at_utc_us"]
            if not 0 <= age <= MAX_REVIEW_AGE // timedelta(microseconds=1):
                raise IngestionError("review_requires_fresh_observation")
            capture = CapturedSnapshot(self.store.load_content(row["content_id"]),
                                       EPOCH + timedelta(microseconds=row["capture_received_at_utc_us"]))
            self._match_capture(row, capture)
            assessment = assess_snapshot(capture.candidate, received_at=capture.received_at,
                                         baseline=self.store.accepted_baseline(), policy=self._stored_policy(row))
            stored = _codes(row["assessment_reasons"], ASSESSMENT_REASONS)
            if (assessment.reasons != stored or not stored
                    or "observation_time_regressed" in stored or REVIEW_REASONS[reason] not in stored):
                raise IngestionError("review_requires_new_observation")
            db.execute("""UPDATE fetch_attempt SET status='accepted',accepted_at_utc_us=?,review_method='operator',
                review_reason_code=?,error_category=NULL,reason_code=NULL WHERE observation_uuid=?""",
                       (_instant(now), reason, observation_uuid))
            db.execute("UPDATE store_state SET last_accepted_attempt_no=?", (row["attempt_no"],))
            return self.result(observation_uuid)
