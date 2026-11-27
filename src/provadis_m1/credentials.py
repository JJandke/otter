"""Explicit, private credential-file loading; never source a shell or export values."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import stat

from .errors import ConfigurationError
from .state import OLD_APPLICATION

MAX_FILE_BYTES = 16384
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LoginCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


def load_credentials(path: Path) -> LoginCredentials:
    path = path.expanduser().absolute()
    resolved = path.resolve()
    if any(candidate.is_relative_to(root)
           for candidate in (path, resolved) for root in (PROJECT_ROOT, OLD_APPLICATION)):
        raise ConfigurationError('credentials_file_must_be_outside_project_and_old_application')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o177):
                raise ConfigurationError('credentials_file_must_be_owner_only_regular_file')
            raw = file.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ConfigurationError('invalid_credentials_file')
        values = {}
        for line in raw.decode('utf-8').splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, separator, value = line.partition('=')
            key = key.strip()
            if (not separator or key not in {'OTTER_USERNAME', 'OTTER_PASSWORD'}
                    or key in values):
                raise ConfigurationError('invalid_credentials_file')
            tokens = shlex.split(value, comments=False, posix=True)
            if len(tokens) != 1 or not tokens[0] or any(ord(c) < 32 for c in tokens[0]):
                raise ConfigurationError('invalid_credentials_file')
            values[key] = tokens[0]
        if set(values) != {'OTTER_USERNAME', 'OTTER_PASSWORD'}:
            raise ConfigurationError('invalid_credentials_file')
        if '@' not in values['OTTER_USERNAME'] or len(values['OTTER_USERNAME']) > 320:
            raise ConfigurationError('invalid_credentials_file')
        return LoginCredentials(values['OTTER_USERNAME'], values['OTTER_PASSWORD'])
    except (OSError, UnicodeError, ValueError):
        raise ConfigurationError('credentials_file_unreadable_or_invalid') from None
