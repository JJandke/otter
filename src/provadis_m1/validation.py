"""Conservative boundary validation; raw timetables are never persisted."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from otter.models import SnapshotCandidate
from otter.normalization import normalize_event

from .errors import AuthenticationError, InvalidResponse, ServiceError, SuspiciousResponse

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedSnapshot:
    # No personal event details in reports or object reprs.
    event_count: int


SnapshotResult = ValidatedSnapshot | SnapshotCandidate
ResponseValidator = Callable[[int, str, bytes], SnapshotResult]


@dataclass(frozen=True, repr=False)
class _EventFields:
    # Whitelist before returning from shared parsing; no upstream metadata.
    title: str
    start: datetime
    end: datetime
    building: str | None
    room: str | None


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidResponse("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise InvalidResponse("non_finite_json_number")


def _date(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise InvalidResponse("invalid_event_date")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidResponse("event_date_missing_offset")
        parsed.astimezone(timezone.utc)  # Check representable UTC range.
        return parsed  # Keep the source offset for the opt-in M2 projection.
    except (ValueError, OverflowError):
        raise InvalidResponse("invalid_event_date") from None


def validate_http(status: int, content_type: str) -> None:
    """Classify known status/header failures without reading a sensitive body."""
    if status in (401, 403):
        # 403 can also mean insufficient permissions; no automatic retry/login loop.
        raise AuthenticationError("api_access_denied")
    if 300 <= status < 400:
        raise AuthenticationError("unexpected_api_redirect")
    if status == 429 or 500 <= status < 600:
        raise ServiceError("api_temporarily_unavailable")
    if status != 200:
        raise InvalidResponse("unexpected_http_status")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not (
        media_type.startswith("application/") and media_type.endswith("+json")
    ):
        raise InvalidResponse("expected_json_content_type")


def _validated_events(
    status: int, content_type: str, body: bytes, *, require_location_keys: bool,
) -> tuple[_EventFields, ...]:
    """Shared strict response validation, independent of snapshot acceptance."""
    validate_http(status, content_type)
    if not body or len(body) > MAX_RESPONSE_BYTES:
        raise InvalidResponse("invalid_response_size")
    try:
        payload = json.loads(
            body.decode("utf-8-sig"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidResponse("malformed_json") from None
    if not isinstance(payload, dict):
        raise InvalidResponse("expected_envelope_object")
    if not {"ok", "data", "count"}.issubset(payload):
        raise InvalidResponse("missing_envelope_fields")
    if payload["ok"] is not True:
        raise InvalidResponse("api_reported_failure")
    if any(
        payload.get(key) is not None and not isinstance(payload[key], str)
        for key in ("message", "message2")
    ):
        raise InvalidResponse("invalid_message_type")
    events, count = payload["data"], payload["count"]
    if not isinstance(events, list) or type(count) is not int or count < 0:
        raise InvalidResponse("invalid_data_or_count_type")
    if count != len(events):
        raise InvalidResponse("count_mismatch")
    projected = []
    for event in events:
        if not isinstance(event, dict):
            raise InvalidResponse("invalid_event_object")
        required = {"eventtitle", "startdate", "enddate"}
        if require_location_keys:
            required |= {"buildingname", "roomname"}
        if not required.issubset(event):
            raise InvalidResponse("missing_event_fields")
        if not isinstance(event["eventtitle"], str) or not event["eventtitle"].strip():
            raise InvalidResponse("empty_event_title")
        for key in ("buildingname", "roomname", "instructorname", "groupname", "email"):
            if key in event and event[key] is not None and not isinstance(event[key], str):
                raise InvalidResponse("invalid_event_text_field")
        end, start = _date(event["enddate"]), _date(event["startdate"])
        if end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
            raise InvalidResponse("invalid_event_interval")
        projected.append(_EventFields(
            title=event["eventtitle"], start=start, end=end,
            building=event.get("buildingname"), room=event.get("roomname"),
        ))
    return tuple(projected)


def validate_response(status: int, content_type: str, body: bytes) -> ValidatedSnapshot:
    """Preserve M1's count-only, nonempty, required-location-key contract."""
    events = _validated_events(status, content_type, body, require_location_keys=True)
    if not events:
        raise SuspiciousResponse()
    return ValidatedSnapshot(event_count=len(events))


def parse_candidate_response(status: int, content_type: str, body: bytes) -> SnapshotCandidate:
    """Opt-in M2 projection, including empty candidates and optional locations.

    Structural validity is not acceptance or proof of source completeness. No
    partial candidate is returned if any row fails validation or normalization.
    """
    events = _validated_events(status, content_type, body, require_location_keys=False)
    return SnapshotCandidate(events=tuple(
        normalize_event(title=event.title, start=event.start, end=event.end,
                        building=event.building, room=event.room)
        for event in events
    ))
