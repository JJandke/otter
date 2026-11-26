"""Ordered, checksummed transactional schema migrations; never executescript."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from ._store_files import StorageError


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256(json.dumps(self.statements, separators=(",", ":")).encode()).hexdigest()


MIGRATIONS = (Migration(1, (
    """CREATE TABLE schema_migration (
        version INTEGER PRIMARY KEY CHECK(version > 0),
        checksum TEXT NOT NULL CHECK(length(checksum)=64),
        applied_at_utc_us INTEGER NOT NULL CHECK(typeof(applied_at_utc_us)='integer'))""",
    """CREATE TABLE snapshot_content (
        content_id INTEGER PRIMARY KEY,
        normalization_version INTEGER NOT NULL CHECK(normalization_version > 0),
        format_version INTEGER NOT NULL CHECK(format_version > 0),
        digest TEXT NOT NULL CHECK(length(digest)=64 AND digest NOT GLOB '*[^0-9a-f]*'),
        event_count INTEGER NOT NULL CHECK(typeof(event_count)='integer' AND event_count >= 0),
        created_at_utc_us INTEGER NOT NULL CHECK(typeof(created_at_utc_us)='integer'),
        UNIQUE(normalization_version, format_version, digest))""",
    """CREATE TABLE snapshot_event (
        content_id INTEGER NOT NULL REFERENCES snapshot_content(content_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal >= 0),
        title TEXT NOT NULL CHECK(typeof(title)='text' AND length(trim(title)) > 0),
        start_utc_us INTEGER NOT NULL CHECK(typeof(start_utc_us)='integer'),
        end_utc_us INTEGER NOT NULL CHECK(typeof(end_utc_us)='integer' AND end_utc_us > start_utc_us),
        start_offset_seconds INTEGER NOT NULL
            CHECK(typeof(start_offset_seconds)='integer' AND abs(start_offset_seconds) < 86400),
        end_offset_seconds INTEGER NOT NULL
            CHECK(typeof(end_offset_seconds)='integer' AND abs(end_offset_seconds) < 86400),
        building TEXT CHECK(building IS NULL OR (typeof(building)='text' AND length(trim(building))>0)),
        room TEXT CHECK(room IS NULL OR (typeof(room)='text' AND length(trim(room))>0)),
        PRIMARY KEY(content_id, ordinal))""",
    """CREATE TABLE fetch_attempt (
        attempt_no INTEGER PRIMARY KEY AUTOINCREMENT,
        observation_uuid TEXT NOT NULL UNIQUE CHECK(length(observation_uuid)=36),
        origin TEXT NOT NULL CHECK(origin IN ('live','import')),
        status TEXT NOT NULL CHECK(status IN
            ('started','accepted','suspicious','invalid','failed','interrupted')),
        started_at_utc_us INTEGER NOT NULL CHECK(typeof(started_at_utc_us)='integer'),
        received_at_utc_us INTEGER CHECK(received_at_utc_us IS NULL OR typeof(received_at_utc_us)='integer'),
        validated_at_utc_us INTEGER CHECK(validated_at_utc_us IS NULL OR typeof(validated_at_utc_us)='integer'),
        finished_at_utc_us INTEGER CHECK(finished_at_utc_us IS NULL OR typeof(finished_at_utc_us)='integer'),
        accepted_at_utc_us INTEGER CHECK(accepted_at_utc_us IS NULL OR typeof(accepted_at_utc_us)='integer'),
        http_status INTEGER CHECK(http_status IS NULL OR
            (typeof(http_status)='integer' AND http_status BETWEEN 100 AND 599)),
        reported_count INTEGER CHECK(reported_count IS NULL OR
            (typeof(reported_count)='integer' AND reported_count >= 0)),
        content_id INTEGER REFERENCES snapshot_content(content_id),
        baseline_attempt_no INTEGER REFERENCES fetch_attempt(attempt_no),
        error_category TEXT CHECK(error_category IS NULL OR error_category IN
            ('authentication','network','service','bootstrap','configuration','storage',
             'invalid_response','suspicious_response','interrupted')),
        reason_code TEXT CHECK(reason_code IS NULL OR
            (length(reason_code) BETWEEN 1 AND 96 AND reason_code NOT GLOB '*[^a-z0-9_]*')),
        policy_version INTEGER CHECK(policy_version IS NULL OR policy_version > 0),
        minimum_future_drop INTEGER CHECK(minimum_future_drop IS NULL OR minimum_future_drop > 0),
        future_drop_percent INTEGER CHECK(future_drop_percent IS NULL OR future_drop_percent BETWEEN 0 AND 100),
        review_method TEXT CHECK(review_method IS NULL OR review_method='operator'),
        CHECK(validated_at_utc_us IS NULL OR received_at_utc_us IS NOT NULL),
        CHECK((status='started' AND finished_at_utc_us IS NULL AND content_id IS NULL
               AND accepted_at_utc_us IS NULL AND received_at_utc_us IS NULL
               AND validated_at_utc_us IS NULL AND reported_count IS NULL)
            OR (status IN ('accepted','suspicious') AND finished_at_utc_us IS NOT NULL
                AND received_at_utc_us IS NOT NULL AND validated_at_utc_us IS NOT NULL
                AND content_id IS NOT NULL AND reported_count IS NOT NULL
                AND ((status='accepted' AND accepted_at_utc_us IS NOT NULL)
                     OR (status='suspicious' AND accepted_at_utc_us IS NULL)))
            OR (status IN ('invalid','failed','interrupted') AND finished_at_utc_us IS NOT NULL
                AND content_id IS NULL AND accepted_at_utc_us IS NULL)))""",
    """CREATE TABLE store_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        stream_uuid TEXT NOT NULL CHECK(length(stream_uuid)=36),
        initialized_at_utc_us INTEGER NOT NULL CHECK(typeof(initialized_at_utc_us)='integer'),
        last_accepted_attempt_no INTEGER REFERENCES fetch_attempt(attempt_no))""",
    "CREATE INDEX attempt_status_order ON fetch_attempt(status, attempt_no)",
    "CREATE INDEX attempt_content ON fetch_attempt(content_id)",
    """CREATE TRIGGER accepted_pointer_insert BEFORE INSERT ON store_state
        WHEN NEW.last_accepted_attempt_no IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM fetch_attempt WHERE attempt_no=NEW.last_accepted_attempt_no AND status='accepted')
        BEGIN SELECT RAISE(ABORT,'invalid_accepted_pointer'); END""",
    """CREATE TRIGGER accepted_pointer_update BEFORE UPDATE OF last_accepted_attempt_no ON store_state
        WHEN (NEW.last_accepted_attempt_no IS NULL AND OLD.last_accepted_attempt_no IS NOT NULL)
          OR (NEW.last_accepted_attempt_no IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM fetch_attempt WHERE attempt_no=NEW.last_accepted_attempt_no AND status='accepted'))
        BEGIN SELECT RAISE(ABORT,'invalid_accepted_pointer'); END""",
    """CREATE TRIGGER attempt_content_count BEFORE UPDATE ON fetch_attempt
        WHEN NEW.status IN ('accepted','suspicious') AND NEW.reported_count !=
            (SELECT event_count FROM snapshot_content WHERE content_id=NEW.content_id)
        BEGIN SELECT RAISE(ABORT,'content_count_mismatch'); END""",
    """CREATE TRIGGER inserted_attempt_content_count BEFORE INSERT ON fetch_attempt
        WHEN NEW.status IN ('accepted','suspicious') AND NEW.reported_count !=
            (SELECT event_count FROM snapshot_content WHERE content_id=NEW.content_id)
        BEGIN SELECT RAISE(ABORT,'content_count_mismatch'); END""",
    """CREATE TRIGGER event_ordinal_bound BEFORE INSERT ON snapshot_event
        WHEN NEW.ordinal >= (SELECT event_count FROM snapshot_content WHERE content_id=NEW.content_id)
        BEGIN SELECT RAISE(ABORT,'invalid_event_ordinal'); END""",
    """CREATE TRIGGER accepted_attempt_immutable BEFORE UPDATE ON fetch_attempt
        WHEN OLD.status='accepted' BEGIN SELECT RAISE(ABORT,'accepted_attempt_immutable'); END""",
    """CREATE TRIGGER accepted_attempt_retained BEFORE DELETE ON fetch_attempt
        WHEN OLD.status='accepted' BEGIN SELECT RAISE(ABORT,'accepted_attempt_retained'); END""",
)),)

# Do not edit migration 1: existing stores retain its shipped checksum.
MIGRATIONS += (Migration(2, (
    "ALTER TABLE fetch_attempt ADD COLUMN capture_digest TEXT CHECK(capture_digest IS NULL OR (length(capture_digest)=64 AND capture_digest NOT GLOB '*[^0-9a-f]*'))",
    "ALTER TABLE fetch_attempt ADD COLUMN capture_normalization_version INTEGER CHECK(capture_normalization_version IS NULL OR capture_normalization_version > 0)",
    "ALTER TABLE fetch_attempt ADD COLUMN capture_format_version INTEGER CHECK(capture_format_version IS NULL OR capture_format_version > 0)",
    "ALTER TABLE fetch_attempt ADD COLUMN capture_event_count INTEGER CHECK(capture_event_count IS NULL OR (typeof(capture_event_count)='integer' AND capture_event_count >= 0))",
    """ALTER TABLE fetch_attempt ADD COLUMN capture_received_at_utc_us INTEGER CHECK(
        (capture_digest IS NULL AND capture_normalization_version IS NULL
         AND capture_format_version IS NULL AND capture_event_count IS NULL AND capture_received_at_utc_us IS NULL)
        OR (capture_digest IS NOT NULL AND capture_normalization_version IS NOT NULL
            AND capture_format_version IS NOT NULL AND capture_event_count IS NOT NULL
            AND typeof(capture_received_at_utc_us)='integer'))""",
    "ALTER TABLE fetch_attempt ADD COLUMN assessment_reasons TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE fetch_attempt ADD COLUMN assessment_information TEXT NOT NULL DEFAULT '[]'",
    """ALTER TABLE fetch_attempt ADD COLUMN review_reason_code TEXT CHECK(
        review_reason_code IS NULL OR review_reason_code IN
            ('confirmed_empty','confirmed_duplicates','confirmed_change'))""",
    """CREATE TRIGGER captured_receipt_immutable BEFORE UPDATE ON fetch_attempt
        WHEN OLD.capture_digest IS NOT NULL AND (
            NEW.capture_digest IS NOT OLD.capture_digest OR
            NEW.capture_normalization_version IS NOT OLD.capture_normalization_version OR
            NEW.capture_format_version IS NOT OLD.capture_format_version OR
            NEW.capture_event_count IS NOT OLD.capture_event_count OR
            NEW.capture_received_at_utc_us IS NOT OLD.capture_received_at_utc_us OR
            NEW.observation_uuid IS NOT OLD.observation_uuid OR NEW.origin IS NOT OLD.origin OR
            NEW.started_at_utc_us IS NOT OLD.started_at_utc_us OR
            NEW.policy_version IS NOT OLD.policy_version OR
            NEW.minimum_future_drop IS NOT OLD.minimum_future_drop OR
            NEW.future_drop_percent IS NOT OLD.future_drop_percent)
        BEGIN SELECT RAISE(ABORT,'captured_receipt_immutable'); END""",
)),)


def check_schema(connection: sqlite3.Connection, *, require_current: bool = True) -> int:
    if tuple(m.version for m in MIGRATIONS) != tuple(range(1, len(MIGRATIONS) + 1)):
        raise StorageError("invalid_migration_sequence")
    present = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migration'").fetchone()
    if present is None:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise StorageError("unrecognized_store_schema")
        version = 0
    else:
        rows = connection.execute("SELECT version,checksum FROM schema_migration ORDER BY version").fetchall()
        if not rows:
            raise StorageError("invalid_migration_history")
        if rows[-1][0] > len(MIGRATIONS):
            raise StorageError("schema_newer_than_application")
        if [r[0] for r in rows] != list(range(1, len(rows) + 1)):
            raise StorageError("invalid_migration_history")
        if any(row[1] != migration.checksum for row, migration in zip(rows, MIGRATIONS)):
            raise StorageError("schema_checksum_mismatch")
        version = len(rows)
    if require_current and version != len(MIGRATIONS):
        raise StorageError("schema_migration_required")
    return version


def apply_pending(connection: sqlite3.Connection, version: int, applied_at: int) -> None:
    """Caller owns a short SQL transaction and the application lock."""
    for migration in MIGRATIONS[version:]:
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migration VALUES (?,?,?)",
                           (migration.version, migration.checksum, applied_at))
    if version == 0:
        connection.execute("INSERT INTO store_state VALUES (1,?,?,NULL)",
                           (str(uuid4()), applied_at))
