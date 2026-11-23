import os
import tempfile
import unittest
from pathlib import Path

from provadis_m1.errors import ConfigurationError
from provadis_m1.state import private_state


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # /var is a symlink on macOS. Resolve the test parent before applying link checks.
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / ".m1-state"
        self.previous_umask = os.umask(0o077)

    def tearDown(self):
        os.umask(self.previous_umask)
        self.temp.cleanup()

    def test_private_profile_and_reopen(self):
        with private_state(self.path) as profile:
            self.assertEqual(profile, self.path / "profile")
            self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
        with private_state(self.path):
            pass

    def test_concurrent_access_rejected(self):
        with private_state(self.path):
            with self.assertRaises(ConfigurationError):
                with private_state(self.path):
                    pass

    def test_unrecognized_directory_is_not_modified(self):
        self.path.mkdir(mode=0o700)
        existing = self.path / "existing"
        existing.write_text("preserve")
        with self.assertRaises(ConfigurationError):
            with private_state(self.path):
                pass
        self.assertEqual(existing.read_text(), "preserve")
        self.assertFalse((self.path / ".m1-owner").exists())

    def test_world_readable_directory_is_rejected_without_chmod(self):
        self.path.mkdir(mode=0o755)
        self.path.chmod(0o755)
        with self.assertRaises(ConfigurationError):
            with private_state(self.path):
                pass
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o755)

    def test_symlink_directory_is_rejected(self):
        target = self.root / "other"
        target.mkdir()
        self.path.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ConfigurationError):
            with private_state(self.path):
                pass

    def test_old_application_and_ancestor_overlap_rejected(self):
        with self.assertRaises(ConfigurationError):
            with private_state(Path("/home/ubuntu/provadis_timetable/.m1-state")):
                pass
        from provadis_m1.state import _overlap
        self.assertTrue(_overlap(Path("/home"), Path("/home/ubuntu/provadis_timetable")))

    def test_normal_profile_directory_name_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            with private_state(self.root / "Chrome"):
                pass
