"""Small fail-closed publication guard. It is a guard, not a full secret scanner.

Check all indexed files, including staged additions. Never print matched contents.
Run before committing/pushing; no production data is needed by this repository.
"""

import re
import subprocess
from pathlib import Path

BAD_PARTS = {".otter-data", ".m1-acceptance", ".m1-login-tests", ".m1-state", ".provadis-m1", ".m1-gui", ".m1-browsers", "profile", "profiles", ".auth"}
BAD_SUFFIXES = {".har", ".pem", ".key", ".passwd", ".sqlite", ".db", ".ics", ".jsonl"}
PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
    re.compile(rb'"(?:access_token|refresh_token|id_token)"\s*:\s*"[^"\s]{20,}"'),
]


def main() -> int:
    names = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    bad = []
    for raw_name in filter(None, names):
        path = Path(raw_name.decode("utf-8"))
        if (BAD_PARTS.intersection(path.parts) or path.suffix in BAD_SUFFIXES
                or ".sqlite" in path.name.lower() or re.search(r"\.db(?:$|[-.])", path.name, re.I)
                or path.name.startswith(".env") or path.suffix == ".env" or path.is_symlink()):
            bad.append(str(path))
            continue
        content = subprocess.check_output(["git", "show", f":{path.as_posix()}"])
        if any(pattern.search(content) for pattern in PATTERNS):
            bad.append(str(path))
    if bad:
        print("Publication check failed; review indexed files (contents withheld):")
        for path in bad:
            print(path)
        return 1
    print("Publication check passed for indexed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
