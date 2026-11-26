"""Private local store files, locks and fixed storage errors (macOS/Linux)."""

import fcntl
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from uuid import uuid4

MARKER = "otter-sqlite-private-store-v1\n"
DATABASE = "otter.sqlite3"
BUSY_MS = 5000
BACKUP_SECONDS = 60
FORBIDDEN = {".git", ".otter-runtime", ".m1-state", ".provadis-m1", "profile", "profiles", ".auth",
             ".m1-acceptance", ".m1-login-tests", ".m1-gui", ".m1-browsers"}


class StorageError(Exception):
    """Only fixed reason codes leave storage; no SQL, paths or payload values."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@contextmanager
def storage_errors():
    try:
        yield
    except sqlite3.Error as error:
        code = getattr(error, "sqlite_errorcode", 0) & 255
        reason = "storage_busy" if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else (
            "storage_constraint_failed" if code == sqlite3.SQLITE_CONSTRAINT else "storage_sql_failed")
        raise StorageError(reason) from None
    except OSError:
        raise StorageError("storage_io_failed") from None


def private_file(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise StorageError("unsafe_store_file")


def private_directory(path: Path) -> None:
    if (".." in path.parts or any(part in FORBIDDEN for part in path.parts)
            or any(p.is_symlink() for p in (path, *path.parents))
            or Path("/home/ubuntu/provadis_timetable") in (path, *path.parents)):
        raise StorageError("unsafe_store_directory")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise StorageError("store_directory_must_be_owned_and_private")


def reserve(path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        os.fchmod(file.fileno(), 0o600)


def checked_directory(directory: Path, *, initialize: bool = False) -> Path:
    path = directory.expanduser().absolute()
    if initialize and not path.exists():
        # Check ancestors before creating anything, including symlinked /tmp.
        if (".." in path.parts or any(p.is_symlink() for p in (path, *path.parents))
                or FORBIDDEN.intersection(path.parts)):
            raise StorageError("unsafe_store_directory")
        if Path("/home/ubuntu/provadis_timetable") in (path, *path.parents):
            raise StorageError("unsafe_store_directory")
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    private_directory(path)
    marker = path / ".owner"
    if initialize and not marker.exists():
        if any(path.iterdir()):
            raise StorageError("unrecognized_store_directory")
        reserve(marker)
        marker.write_text(MARKER, encoding="utf-8")
        sync_file(marker)
        sync_directory(path)
    private_file(marker)
    if marker.read_text(encoding="utf-8") != MARKER:
        raise StorageError("invalid_store_marker")
    return path


@contextmanager
def exclusive_lock(directory: Path):
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as lock:
        private_file(directory / ".lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StorageError("store_already_in_use") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def connect(path: Path) -> sqlite3.Connection:
    private_file(path)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private_file(sidecar)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, autocommit=True,
                                 timeout=BUSY_MS / 1000)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA busy_timeout={BUSY_MS}")
        if (connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                or connection.execute("PRAGMA synchronous").fetchone()[0] != 2
                or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise StorageError("unsupported_store_configuration")
        return connection
    except BaseException:
        connection.close()
        raise


def integrity_check(connection: sqlite3.Connection, *, full: bool = False) -> None:
    pragma = "integrity_check" if full else "quick_check"
    if ([row[0] for row in connection.execute(f"PRAGMA {pragma}")] != ["ok"]
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
        raise StorageError("store_integrity_failed")


def sync_file(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migration_backup(connection: sqlite3.Connection, directory: Path, version: int) -> Path:
    """Publish an independently verified pre-migration copy without overwriting."""
    parent = directory / "backups"
    if not parent.exists():
        parent.mkdir(mode=0o700)
        sync_directory(directory)
    private_directory(parent)
    target = parent / f"pre-migration-v{version}-{uuid4().hex}.sqlite3"
    temporary = parent / f".migration-{uuid4().hex}.partial.sqlite3"
    reserve(temporary)
    started = monotonic()

    def progress(_status, _remaining, _total):
        if monotonic() - started > BACKUP_SECONDS:
            raise StorageError("migration_backup_timeout")

    try:
        destination = connect(temporary)
        try:
            connection.backup(destination, pages=128, progress=progress, sleep=0.05)
            integrity_check(destination, full=True)
        finally:
            destination.close()
        sync_file(temporary)
        os.link(temporary, target)
        temporary.unlink()
        sync_directory(parent)
        return target
    finally:
        temporary.unlink(missing_ok=True)  # Never remove a published backup.
