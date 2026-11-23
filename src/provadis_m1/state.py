"""Private, marked state directory and a process lock (macOS/Linux)."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import ConfigurationError

OLD_APPLICATION = Path("/home/ubuntu/provadis_timetable")
MARKER = "provadis-m1-private-state-v1\n"


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _has_symlink(path: Path) -> bool:
    return any(p.is_symlink() for p in (path, *path.parents))


@contextmanager
def private_state(path: Path) -> Iterator[Path]:
    """Use only dedicated state; reject links, shared directories, old app paths."""
    path = path.absolute()
    if path.name not in {".m1-state", ".provadis-m1"}:
        raise ConfigurationError("state_directory_must_have_dedicated_name")
    if _has_symlink(path) or ".git" in path.parts or _overlap(path.resolve(), OLD_APPLICATION):
        raise ConfigurationError("unsafe_state_directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConfigurationError("state_directory_must_be_owned_and_private")
    marker = path / ".m1-owner"
    if not marker.exists():
        if any(path.iterdir()):
            raise ConfigurationError("unrecognized_nonempty_state_directory")
        with marker.open("x", encoding="utf-8") as file:
            file.write(MARKER)
        marker.chmod(0o600)
    if marker.is_symlink() or marker.read_text(encoding="utf-8") != MARKER:
        raise ConfigurationError("invalid_state_marker")
    lock_path = path / ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError("profile_already_in_use") from None
        profile = path / "profile"
        if _has_symlink(profile):
            raise ConfigurationError("unsafe_profile_directory")
        profile.mkdir(mode=0o700, exist_ok=True)
        if profile.stat().st_mode & 0o077 or profile.stat().st_uid != os.getuid():
            raise ConfigurationError("profile_directory_must_be_owned_and_private")
        try:
            yield profile
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
