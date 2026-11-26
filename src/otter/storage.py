"""Embedded SQLite repository and explicit maintenance CLI; no live ingestion."""

import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from provadis_m1.errors import InvalidResponse

from . import migrations
from ._store_files import (
    DATABASE, StorageError, checked_directory, connect, exclusive_lock, integrity_check,
    migration_backup, reserve, storage_errors, sync_directory, sync_file,
)
from .assessment import AcceptedBaseline
from .models import NormalizedEvent, SnapshotCandidate
from .normalization import (
    EPOCH, canonical_snapshot_bytes, fingerprint_snapshot, normalize_event, utc_microseconds,
)


def _time(value: datetime | None = None) -> int:
    try:
        return utc_microseconds(datetime.now(timezone.utc) if value is None else value)
    except (InvalidResponse, AttributeError, TypeError):
        raise StorageError("storage_time_must_be_aware") from None


def _validate_candidate(candidate: SnapshotCandidate) -> None:
    """Revalidate repository inputs/rows, including manually constructed models."""
    if not isinstance(candidate, SnapshotCandidate) or candidate.normalization_version != 1:
        raise StorageError("unsupported_candidate_version")
    for event in candidate.events:
        if (not isinstance(event.title, str)
                or any(v is not None and not isinstance(v, str) for v in (event.building, event.room))
                or any(type(v) is not int for v in (event.start_utc_us, event.end_utc_us,
                                                   event.start_offset_seconds, event.end_offset_seconds))):
            raise StorageError("invalid_normalized_content")
        try:
            start = (EPOCH + timedelta(microseconds=event.start_utc_us)).astimezone(
                timezone(timedelta(seconds=event.start_offset_seconds)))
            end = (EPOCH + timedelta(microseconds=event.end_utc_us)).astimezone(
                timezone(timedelta(seconds=event.end_offset_seconds)))
            normalized = normalize_event(title=event.title, start=start, end=end,
                                         building=event.building, room=event.room)
        except Exception:
            raise StorageError("invalid_normalized_content") from None
        if not event.title.strip() or normalized != event:
            raise StorageError("invalid_normalized_content")


def _state_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT * FROM store_state").fetchall()
    if len(rows) != 1 or rows[0]["singleton"] != 1:
        raise StorageError("invalid_store_state")
    pointer = rows[0]["last_accepted_attempt_no"]
    latest = connection.execute("SELECT max(attempt_no) FROM fetch_attempt WHERE status='accepted'").fetchone()[0]
    if pointer != latest:
        raise StorageError("invalid_accepted_pointer")
    if pointer is not None and connection.execute(
        "SELECT 1 FROM fetch_attempt WHERE attempt_no=? AND status='accepted'", (pointer,)
    ).fetchone() is None:
        raise StorageError("invalid_accepted_pointer")


def initialize_store(directory: Path, *, now: datetime | None = None) -> None:
    """Publish a fully initialized DB without replacing an existing store."""
    with storage_errors():
        directory = checked_directory(directory, initialize=True)
        with exclusive_lock(directory):
            target = directory / DATABASE
            if target.exists() or target.is_symlink():
                raise StorageError("store_already_exists")
            temporary = directory / f".init-{uuid4().hex}.sqlite3"
            reserve(temporary)
            connection = None
            try:
                connection = connect(temporary)
                connection.execute("BEGIN IMMEDIATE")
                migrations.apply_pending(connection, 0, _time(now))
                _state_check(connection)
                connection.execute("COMMIT")
                migrations.check_schema(connection)
                integrity_check(connection, full=True)
                connection.close()
                connection = None
                sync_file(temporary)
                # link is atomic and, unlike replace(), cannot overwrite target.
                os.link(temporary, target)
                temporary.unlink()
                sync_directory(directory)
            finally:
                if connection is not None:
                    connection.close()  # Rolls back an uncommitted initialization.
                temporary.unlink(missing_ok=True)


def migrate_store(directory: Path, *, now: datetime | None = None) -> bool:
    """Explicit upgrade only; preserve a verified pre-migration backup."""
    with storage_errors():
        directory = checked_directory(directory)
        with exclusive_lock(directory):
            connection = connect(directory / DATABASE)
            try:
                integrity_check(connection, full=True)
                version = migrations.check_schema(connection, require_current=False)
                if version == 0:
                    raise StorageError("uninitialized_store_schema")
                _state_check(connection)
                if version == len(migrations.MIGRATIONS):
                    return False
                migration_backup(connection, directory, version)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    migrations.apply_pending(connection, version, _time(now))
                    migrations.check_schema(connection)
                    _state_check(connection)
                    integrity_check(connection, full=True)
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                return True
            finally:
                connection.close()


class Store:
    """One connection per thread; every write requires transaction().

    transaction() yields a low-level SQL connection for the later ingestion
    orchestrator. It is trusted code, not a public arbitrary-SQL input channel.
    """

    def __init__(self, directory: Path):
        self._connection = None
        self._lock_depth = 0
        self._writing = False
        with storage_errors():
            self.directory = checked_directory(directory)
            connection = connect(self.directory / DATABASE)  # mode=rw never creates.
            try:
                integrity_check(connection)
                migrations.check_schema(connection)
                _state_check(connection)
            except BaseException:
                connection.close()
                raise
            self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("store_is_closed")
        return self._connection

    @contextmanager
    def exclusive(self):
        """Reentrant for this Store, enabling Issue #7's whole-ingestion lock."""
        with storage_errors():
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
            else:
                with exclusive_lock(self.directory):
                    self._lock_depth = 1
                    try:
                        yield
                    finally:
                        self._lock_depth = 0

    @contextmanager
    def transaction(self):
        with self.exclusive(), storage_errors():
            connection = self._db()
            if connection.in_transaction:
                raise StorageError("nested_store_transaction")
            try:
                connection.execute("BEGIN IMMEDIATE")
                migrations.check_schema(connection)
                self._writing = True
                yield connection
                _state_check(connection)
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._writing = False

    @contextmanager
    def _read(self):
        with storage_errors():
            connection = self._db()
            own = not connection.in_transaction
            if own:
                connection.execute("BEGIN")
            try:
                yield connection
            finally:
                if own and connection.in_transaction:
                    connection.execute("ROLLBACK")  # Read-only transaction.

    def _require_write(self) -> sqlite3.Connection:
        if not self._writing:
            raise StorageError("store_write_requires_transaction")
        return self._db()

    def begin_attempt(self, observation_uuid: str, *, origin: str, started_at: datetime) -> int:
        connection = self._require_write()
        try:
            valid = isinstance(observation_uuid, str) and str(UUID(observation_uuid)) == observation_uuid
        except ValueError:
            valid = False
        if not valid or origin not in ("live", "import"):
            raise StorageError("invalid_attempt_context")
        with storage_errors():
            cursor = connection.execute(
                "INSERT INTO fetch_attempt(observation_uuid,origin,status,started_at_utc_us) VALUES (?,?,'started',?)",
                (observation_uuid, origin, _time(started_at)))
            return cursor.lastrowid

    def put_content(self, candidate: SnapshotCandidate, *, created_at: datetime) -> int:
        connection = self._require_write()
        _validate_candidate(candidate)
        fingerprint = fingerprint_snapshot(candidate)
        with storage_errors():
            row = connection.execute(
                "SELECT content_id FROM snapshot_content WHERE normalization_version=? AND format_version=? AND digest=?",
                (fingerprint.normalization_version, fingerprint.format_version, fingerprint.sha256)).fetchone()
            if row is not None:
                existing = self.load_content(row[0])
                if canonical_snapshot_bytes(existing) != canonical_snapshot_bytes(candidate):
                    raise StorageError("content_digest_conflict")
                return row[0]
            cursor = connection.execute(
                "INSERT INTO snapshot_content(normalization_version,format_version,digest,event_count,created_at_utc_us) VALUES (?,?,?,?,?)",
                (fingerprint.normalization_version, fingerprint.format_version, fingerprint.sha256,
                 fingerprint.event_count, _time(created_at)))
            content_id = cursor.lastrowid
            rows = json.loads(canonical_snapshot_bytes(candidate))["events"]
            connection.executemany("INSERT INTO snapshot_event VALUES (?,?,?,?,?,?,?,?,?)",
                                   ((content_id, index, *event) for index, event in enumerate(rows)))
            return content_id

    def load_content(self, content_id: int) -> SnapshotCandidate:
        with self._read() as connection:
            header = connection.execute("SELECT * FROM snapshot_content WHERE content_id=?", (content_id,)).fetchone()
            if header is None:
                raise StorageError("content_not_found")
            if (header["normalization_version"], header["format_version"]) != (1, 1):
                raise StorageError("unsupported_content_version")
            rows = connection.execute("SELECT * FROM snapshot_event WHERE content_id=? ORDER BY ordinal",
                                      (content_id,)).fetchall()
            if len(rows) != header["event_count"] or [r["ordinal"] for r in rows] != list(range(len(rows))):
                raise StorageError("content_integrity_failed")
            candidate = SnapshotCandidate(events=tuple(NormalizedEvent(*tuple(row)[2:]) for row in rows))
            _validate_candidate(candidate)
            if fingerprint_snapshot(candidate).sha256 != header["digest"]:
                raise StorageError("content_integrity_failed")
            return candidate

    def last_accepted(self) -> dict | None:
        with self._read() as connection:
            row = connection.execute("SELECT a.* FROM store_state s JOIN fetch_attempt a ON a.attempt_no=s.last_accepted_attempt_no").fetchone()
            return dict(row) if row is not None else None

    def attempt(self, observation_uuid: str) -> dict | None:
        """Internal receipt metadata may include a private content digest."""
        with self._read() as connection:
            row = connection.execute("SELECT * FROM fetch_attempt WHERE observation_uuid=?",
                                     (observation_uuid,)).fetchone()
            return dict(row) if row is not None else None

    def latest_attempt_no(self) -> int | None:
        with self._read() as connection:
            return connection.execute("SELECT max(attempt_no) FROM fetch_attempt").fetchone()[0]

    @staticmethod
    def _safe_attempt_metadata(row: dict | None) -> dict | None:
        if row is None:
            return None
        # Keep private capture digests and raw stored JSON out of CLI status.
        return {key: value for key, value in row.items()
                if not key.startswith("capture_") and key not in
                {"assessment_reasons", "assessment_information"}}

    def accepted_baseline(self) -> AcceptedBaseline | None:
        with self._read():
            row = self.last_accepted()
            if row is None:
                return None
            return AcceptedBaseline(self.load_content(row["content_id"]),
                                    EPOCH + timedelta(microseconds=row["received_at_utc_us"]))

    def last_successful_fetch(self) -> dict | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM fetch_attempt WHERE origin='live' AND validated_at_utc_us IS NOT NULL ORDER BY attempt_no DESC LIMIT 1").fetchone()
            return dict(row) if row is not None else None

    def status(self) -> dict:
        with self._read() as connection:
            _state_check(connection)
            return {"schema_version": migrations.check_schema(connection),
                    "sqlite_version": sqlite3.sqlite_version,
                    "attempt_count": connection.execute("SELECT count(*) FROM fetch_attempt").fetchone()[0],
                    "content_count": connection.execute("SELECT count(*) FROM snapshot_content").fetchone()[0],
                    "event_row_count": connection.execute("SELECT count(*) FROM snapshot_event").fetchone()[0],
                    "last_accepted": self._safe_attempt_metadata(self.last_accepted()),
                    "last_successful_fetch": self._safe_attempt_metadata(self.last_successful_fetch())}

    def schema(self) -> list[dict]:
        with self._read() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="OTTER private SQLite maintenance (no live fetch)")
    cli.add_argument("command", choices=("init", "status", "schema", "migrate"))
    cli.add_argument("--directory", type=Path, default=Path(".otter-data"))
    args = cli.parse_args(argv)
    try:
        if args.command == "init":
            initialize_store(args.directory)
        elif args.command == "migrate":
            migrate_store(args.directory)
        with Store(args.directory) as store:
            result = store.schema() if args.command == "schema" else store.status()
        print(json.dumps({"ok": True, "result": result}, separators=(",", ":")))
        return 0
    except StorageError as error:
        print(json.dumps({"ok": False, "reason": error.reason}, separators=(",", ":")))
        return 1
    except Exception:
        print('{"ok":false,"reason":"unexpected_local_storage_error"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
