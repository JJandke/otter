"""Private SQLite backups and restore into a new store; never replace live files."""

import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

from . import migrations
from ._store_files import (
    BACKUP_SECONDS, DATABASE, FORBIDDEN, StorageError, checked_directory, connect, exclusive_lock,
    integrity_check, private_directory, private_file, reserve, storage_errors,
    sync_directory, sync_file,
)
from .storage import _load_content, _state_check


def _schema_shape(connection):
    return [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name")]


def _inspect(connection: sqlite3.Connection) -> dict:
    """Verify every content object, not only the current accepted baseline."""
    integrity_check(connection, full=True)
    version = migrations.check_schema(connection, require_current=False)
    if not version:
        raise StorageError("uninitialized_store_schema")
    with closing(sqlite3.connect(":memory:", autocommit=True)) as expected:
        for migration in migrations.MIGRATIONS[:version]:
            for statement in migration.statements:
                expected.execute(statement)
        if _schema_shape(connection) != _schema_shape(expected):
            raise StorageError("backup_schema_mismatch")
    _state_check(connection)
    if connection.execute("""SELECT 1 FROM fetch_attempt a JOIN snapshot_content c USING(content_id)
        WHERE a.reported_count IS NOT c.event_count LIMIT 1""").fetchone():
        raise StorageError("backup_observation_mismatch")
    if version >= 2 and connection.execute("""SELECT 1 FROM fetch_attempt a
        JOIN snapshot_content c USING(content_id) WHERE a.capture_digest IS NOT NULL AND (
            a.capture_digest IS NOT c.digest OR a.capture_normalization_version IS NOT c.normalization_version
            OR a.capture_format_version IS NOT c.format_version OR a.capture_event_count IS NOT c.event_count
            OR a.capture_received_at_utc_us IS NOT a.received_at_utc_us) LIMIT 1""").fetchone():
        raise StorageError("backup_observation_mismatch")
    state = connection.execute("SELECT * FROM store_state").fetchone()
    try:
        if str(UUID(state["stream_uuid"])) != state["stream_uuid"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise StorageError("invalid_store_state") from None
    expired = 0
    for header in connection.execute("SELECT * FROM snapshot_content ORDER BY content_id"):
        if (header["normalization_version"], header["format_version"]) != (1, 1):
            raise StorageError("unsupported_content_version")
        if "expired_at_utc_us" in header.keys() and header["expired_at_utc_us"] is not None:
            expired += 1
            if (connection.execute("SELECT 1 FROM snapshot_event WHERE content_id=?", (header["content_id"],)).fetchone()
                    or connection.execute("SELECT 1 FROM fetch_attempt WHERE content_id=? AND status!='suspicious'",
                                          (header["content_id"],)).fetchone()
                    or not connection.execute("SELECT 1 FROM fetch_attempt WHERE content_id=? AND status='suspicious'",
                                              (header["content_id"],)).fetchone()):
                raise StorageError("invalid_content_expiry")
        else:
            _load_content(connection, header["content_id"])
    accepted = connection.execute(
        "SELECT received_at_utc_us FROM fetch_attempt WHERE attempt_no=?",
        (state["last_accepted_attempt_no"],)).fetchone()
    successful = connection.execute("""SELECT received_at_utc_us FROM fetch_attempt
        WHERE origin='live' AND validated_at_utc_us IS NOT NULL ORDER BY attempt_no DESC LIMIT 1""").fetchone()
    return {"schema_version": version, "stream_uuid": state["stream_uuid"],
            "attempt_count": connection.execute("SELECT count(*) FROM fetch_attempt").fetchone()[0],
            "content_count": connection.execute("SELECT count(*) FROM snapshot_content").fetchone()[0],
            "event_row_count": connection.execute("SELECT count(*) FROM snapshot_event").fetchone()[0],
            "expired_content_count": expired,
            "started_count": connection.execute("SELECT count(*) FROM fetch_attempt WHERE status='started'").fetchone()[0],
            "last_accepted_attempt_no": state["last_accepted_attempt_no"],
            "last_accepted_received_at_utc_us": accepted[0] if accepted else None,
            "last_successful_received_at_utc_us": successful[0] if successful else None}


@contextmanager
def _readonly_backup(path: Path):
    path = path.expanduser().absolute()
    private_directory(path.parent)
    private_file(path)
    if path.suffix != ".sqlite3" or path.name == DATABASE or path.name.startswith("."):
        raise StorageError("invalid_backup_file")
    if any(p.exists() or p.is_symlink() for p in
           (path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm"))):
        raise StorageError("backup_has_sidecars")
    # Published backups are standalone immutable files, never live store DBs.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, autocommit=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise StorageError("unsupported_store_configuration")
        yield connection
    finally:
        connection.close()


def verify_backup(path: Path) -> dict:
    """Read only. A supported older version can be restored, then migrated explicitly."""
    with storage_errors(), _readonly_backup(path) as connection:
        return _inspect(connection)


def check_store(directory: Path) -> dict:
    """Full maintenance check without migration, recovery, acceptance or fetch."""
    with storage_errors():
        directory = checked_directory(directory)
        with exclusive_lock(directory):
            connection = connect(directory / DATABASE)
            try:
                return _inspect(connection)
            finally:
                connection.close()


def _copy(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    started = monotonic()

    def progress(_status, _remaining, _total):
        if monotonic() - started > BACKUP_SECONDS:
            raise StorageError("backup_timeout")

    source.backup(target, pages=128, progress=progress, sleep=0.05)


def _new_backup_directory(directory: Path) -> Path:
    directory = directory.expanduser().absolute()
    # Check ancestors before mkdir; private_directory also excludes auth/production.
    if (any(p.is_symlink() for p in (directory, *directory.parents))
            or FORBIDDEN.intersection(directory.parts)
            or Path("/home/ubuntu/provadis_timetable") in (directory, *directory.parents)):
        raise StorageError("unsafe_store_directory")
    if not directory.exists():
        # No mkdir(parents=True): never repurpose arbitrary directory trees.
        directory.mkdir(mode=0o700)
        sync_directory(directory.parent)
    private_directory(directory)
    return directory


def _create_backup(connection: sqlite3.Connection, directory: Path,
                   backup_directory: Path | None = None) -> dict:
    """Caller holds the store lock. Publish only a fully verified independent DB."""
    report = _inspect(connection)
    parent = (directory / "backups" if backup_directory is None else backup_directory).expanduser().absolute()
    if any(p.is_symlink() for p in (parent, *parent.parents)):
        raise StorageError("unsafe_store_directory")
    parent, directory = parent.resolve(), directory.resolve()
    if parent == directory or (directory in parent.parents and parent != directory / "backups"):
        raise StorageError("unsafe_backup_directory")
    parent = _new_backup_directory(parent)
    name = f"snapshot-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex}.sqlite3"
    target, temporary = parent / name, parent / f".backup-{uuid4().hex}.partial.sqlite3"
    reserve(temporary)
    try:
        destination = connect(temporary)
        try:
            _copy(connection, destination)
            if _inspect(destination) != report:
                raise StorageError("backup_content_mismatch")
        finally:
            destination.close()
        sync_file(temporary)
        os.link(temporary, target)  # Atomic no-overwrite publication.
        temporary.unlink()
        sync_directory(parent)
        return {"backup_name": name, **report}
    finally:
        temporary.unlink(missing_ok=True)


def backup_store(directory: Path, *, backup_directory: Path | None = None) -> dict:
    with storage_errors():
        directory = checked_directory(directory)
        with exclusive_lock(directory):
            connection = connect(directory / DATABASE)
            try:
                return _create_backup(connection, directory, backup_directory)
            finally:
                connection.close()


def restore_backup(path: Path, directory: Path) -> dict:
    """Create a new marked store, preserving original stream/observation identities.

    Never replaces a database or journal. Operational promotion is a separate,
    deliberate stopped-writer step described in the recovery runbook.
    """
    with storage_errors(), _readonly_backup(path) as source:
        report = _inspect(source)  # Bad input creates no destination.
        directory = checked_directory(directory, initialize=True)
        with exclusive_lock(directory):
            target = directory / DATABASE
            if target.exists() or target.is_symlink():
                raise StorageError("store_already_exists")
            if any(p.name not in {".owner", ".lock"} for p in directory.iterdir()):
                raise StorageError("restore_destination_not_empty")
            temporary = directory / f".restore-{uuid4().hex}.partial.sqlite3"
            reserve(temporary)
            try:
                destination = connect(temporary)
                try:
                    _copy(source, destination)
                    if _inspect(destination) != report:
                        raise StorageError("backup_content_mismatch")
                finally:
                    destination.close()
                sync_file(temporary)
                os.link(temporary, target)
                temporary.unlink()
                sync_directory(directory)
            finally:
                temporary.unlink(missing_ok=True)
        return {**report, "migration_required": report["schema_version"] != len(migrations.MIGRATIONS)}
