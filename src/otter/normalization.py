"""Pure normalization of validated fields; no clock, network or persistence."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unicodedata import normalize

from provadis_m1.errors import InvalidResponse

from .models import NormalizedEvent, SnapshotCandidate, SnapshotFingerprint

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)
MICROSECOND = timedelta(microseconds=1)
CONTENT_FORMAT_VERSION = 1


def utc_microseconds(value: datetime) -> int:
    """Avoid floating-point timestamps, including for dates before the epoch."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidResponse("event_date_missing_offset")
    try:
        return (value.astimezone(timezone.utc) - EPOCH) // MICROSECOND
    except (ValueError, OverflowError):
        raise InvalidResponse("invalid_event_date") from None


def _offset_seconds(value: datetime) -> int:
    offset = value.utcoffset()
    if offset is None:
        raise InvalidResponse("event_date_missing_offset")
    # Do not silently truncate a fractional-second offset that ISO parsing can
    # accept. The legacy M1 wrapper retains its original broader parsing policy.
    if offset % SECOND:
        raise InvalidResponse("event_offset_requires_whole_seconds")
    return offset // SECOND


def _text(value: str) -> str:
    result = normalize("NFC", value.strip())
    try:
        result.encode("utf-8")
    except UnicodeError:
        raise InvalidResponse("invalid_event_text_field") from None
    return result


def normalize_event(
    *, title: str, start: datetime, end: datetime,
    building: str | None, room: str | None,
) -> NormalizedEvent:
    """Project already type-checked source fields without inventing locations."""
    start_us, end_us = utc_microseconds(start), utc_microseconds(end)
    if end_us <= start_us:
        raise InvalidResponse("invalid_event_interval")
    return NormalizedEvent(
        title=_text(title), start_utc_us=start_us, end_utc_us=end_us,
        start_offset_seconds=_offset_seconds(start), end_offset_seconds=_offset_seconds(end),
        building=(_text(building) or None) if building is not None else None,
        room=(_text(room) or None) if room is not None else None,
    )


def canonical_snapshot_bytes(candidate: SnapshotCandidate) -> bytes:
    """Private, order-independent encoding retaining all duplicate copies.

    JSON-encoded row keys avoid comparing optional strings with None. Tuple
    fields and format version are part of the content contract; changing them
    requires a new format version. Never log these timetable-bearing bytes.
    """
    if not isinstance(candidate, SnapshotCandidate):
        raise TypeError("fingerprint_requires_snapshot_candidate")
    rows = [
        [event.title, event.start_utc_us, event.end_utc_us,
         event.start_offset_seconds, event.end_offset_seconds, event.building, event.room]
        for event in candidate.events
    ]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return json.dumps(
        {"format_version": CONTENT_FORMAT_VERSION,
         "normalization_version": candidate.normalization_version, "events": rows},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def fingerprint_snapshot(candidate: SnapshotCandidate) -> SnapshotFingerprint:
    """Hash normalized contents, excluding fetch times and discarded metadata."""
    return SnapshotFingerprint(
        sha256=hashlib.sha256(canonical_snapshot_bytes(candidate)).hexdigest(),
        normalization_version=candidate.normalization_version,
        format_version=CONTENT_FORMAT_VERSION, event_count=candidate.event_count,
    )
