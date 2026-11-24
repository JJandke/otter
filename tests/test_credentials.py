import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from provadis_m1.credentials import LoginCredentials, load_credentials
from provadis_m1.errors import ConfigurationError

USER = 'synthetic.person@example.invalid'
SECRET = 'SYNTHETIC-password-$-#-not-a-credential'


class CredentialFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'credentials.env'
        self.write(f"OTTER_USERNAME='{USER}'\nOTTER_PASSWORD='{SECRET}'\n")

    def write(self, content):
        self.path.write_text(content)
        self.path.chmod(0o600)

    def test_private_file_loads_without_exposing_values_in_repr(self):
        credentials = load_credentials(self.path)
        self.assertEqual(credentials.username, USER)
        self.assertEqual(credentials.password, SECRET)
        self.assertNotIn(USER, repr(credentials))
        self.assertNotIn(SECRET, repr(credentials))

    def test_values_are_literals_without_shell_or_environment_expansion(self):
        value = '$(touch NEVER_CREATED) $HOME #literal'
        self.write(f"# comment\nOTTER_USERNAME={USER}\nOTTER_PASSWORD='{value}'\n")
        self.assertEqual(load_credentials(self.path).password, value)
        self.assertFalse((self.root / 'NEVER_CREATED').exists())

    def test_group_readable_file_and_wrong_owner_are_rejected(self):
        self.path.chmod(0o640)
        with self.assertRaises(ConfigurationError):
            load_credentials(self.path)
        self.path.chmod(0o600)
        with patch('provadis_m1.credentials.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(ConfigurationError):
                load_credentials(self.path)

    def test_symlink_and_fifo_are_rejected(self):
        link = self.root / 'link.env'
        link.symlink_to(self.path)
        with self.assertRaises(ConfigurationError):
            load_credentials(link)
        fifo = self.root / 'pipe.env'
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ConfigurationError):
            load_credentials(fifo)

    def test_project_and_old_application_are_never_read(self):
        with patch('provadis_m1.credentials.PROJECT_ROOT', self.root):
            with self.assertRaises(ConfigurationError), patch('os.open') as opened:
                load_credentials(self.path)
            opened.assert_not_called()
        with self.assertRaises(ConfigurationError):
            load_credentials(Path('/home/ubuntu/provadis_timetable/credentials.env'))

    def test_malformed_or_oversized_files_fail_without_echoing_contents(self):
        for content in (
            f'OTTER_PASSWORD={SECRET}\n',
            f'OTTER_USERNAME={USER}\nOTTER_PASSWORD={SECRET}\nOTTER_PASSWORD=duplicate\n',
            f'OTTER_USERNAME={USER}\nOTTER_PASSWORD="{SECRET}\n',
            f'OTTER_USERNAME={USER}\nOTTER_PASSWORD=""\n',
            f'export OTTER_USERNAME={USER}\nOTTER_PASSWORD={SECRET}\n',
            f'OTTER_USERNAME={USER}\nOTTER_PASSWORD={SECRET}\nOTHER=not-allowed\n',
            '#' * 17000,
        ):
            with self.subTest(length=len(content)):
                self.write(content)
                with self.assertRaises(ConfigurationError) as raised:
                    load_credentials(self.path)
                self.assertNotIn(SECRET, str(raised.exception))
                self.assertNotIn(USER, str(raised.exception))
