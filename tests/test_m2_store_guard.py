"""Force-staged database artifacts must fail publication checks without secrets."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "check_public_tree.py"


class StorePublicationTests(unittest.TestCase):
    def test_guard_blocks_private_store_database_backups_and_sidecars(self):
        for name in ("otter.sqlite3", "otter.sqlite3-journal", "otter.sqlite3-wal",
                     "backup.sqlite3.bak", "otter.db-shm", ".otter-data/summary.txt",
                     ".otter-runtime/summary.txt", ".otter-backups/summary.txt",
                     "archive.db.bak", "RENAMED.SQLITE3.BAK"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE-CANARY")
                subprocess.run(["git", "add", "--", name], cwd=root, check=True, capture_output=True)
                result = subprocess.run([sys.executable, str(GUARD)], cwd=root,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("PRIVATE-CANARY", result.stdout + result.stderr)

    def test_gitignore_excludes_default_database_and_backup_tree(self):
        root = GUARD.parents[1]
        paths = (".otter-data/otter.sqlite3", ".otter-data/backups/snapshot.txt",
                 "other.sqlite3-journal", "other.db-journal", ".otter-runtime/private.txt",
                 ".otter-backups/private.txt", "archive.db.bak")
        result = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(paths) + "\n",
                                cwd=root, capture_output=True, text=True, env=os.environ)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), list(paths))

    def test_guard_blocks_renamed_sqlite_bytes_without_printing_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            (root / "renamed-export.bin").write_bytes(b"SQLite format 3\x00PRIVATE-CANARY")
            subprocess.run(["git", "add", "--", "renamed-export.bin"], cwd=root, check=True, capture_output=True)
            result = subprocess.run([sys.executable, str(GUARD)], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("PRIVATE-CANARY", result.stdout + result.stderr)
