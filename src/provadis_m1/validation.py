"""Conservative boundary validation; raw timetables are never persisted."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import AuthenticationError, InvalidResponse, ServiceError, SuspiciousResponse

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedSnapshot:
    # No personal event details in reports or object reprs.
    event_count: int


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
        return parsed.astimezone(timezone.utc)
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


def validate_response(status: int, content_type: str, body: bytes) -> ValidatedSnapshot:
    """Only a complete, nonempty HTTP 200 JSON snapshot can be successful.

    This validates structure, not semantic completeness. M1 never updates calendars.
    A genuinely empty timetable is deliberately inconclusive until later design work.
    """
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
    if not {"ok", "message", "message2", "data", "count"}.issubset(payload):
        raise InvalidResponse("missing_envelope_fields")
    if payload["ok"] is not True:
        raise InvalidResponse("api_reported_failure")
    if any(
        payload[key] is not None and not isinstance(payload[key], str)
        for key in ("message", "message2")
    ):
        raise InvalidResponse("invalid_message_type")
    events, count = payload["data"], payload["count"]
    if not isinstance(events, list) or type(count) is not int or count < 0:
        raise InvalidResponse("invalid_data_or_count_type")
    if count != len(events):
        raise InvalidResponse("count_mismatch")
    for event in events:
        if not isinstance(event, dict):
            raise InvalidResponse("invalid_event_object")
        required = {"eventtitle", "startdate", "enddate", "buildingname", "roomname"}
        if not required.issubset(event):
            raise InvalidResponse("missing_event_fields")
        if not isinstance(event["eventtitle"], str) or not event["eventtitle"].strip():
            raise InvalidResponse("empty_event_title")
        for key in ("buildingname", "roomname", "instructorname", "groupname", "email"):
            if key in event and event[key] is not None and not isinstance(event[key], str):
                raise InvalidResponse("invalid_event_text_field")
        if _date(event["enddate"]) <= _date(event["startdate"]):
            raise InvalidResponse("invalid_event_interval")
    if not events:
        raise SuspiciousResponse()
    return ValidatedSnapshot(event_count=count)
