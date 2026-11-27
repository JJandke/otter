import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from provadis_m1.failure_diagnostics import capture_failure


class FailureDiagnosticsTests(unittest.TestCase):
    def test_projection_copies_only_fixed_labels_and_numeric_geometry(self):
        page = Mock(url="https://login.microsoftonline.com/common/login?code=SECRET#TOKEN")
        page.content.side_effect = AssertionError("must not read raw DOM")
        page.screenshot.side_effect = AssertionError("must not capture original pixels")
        locator = Mock()
        locator.filter.return_value = locator
        locator.count.return_value = 1
        locator.bounding_box.return_value = {"x": 10, "y": 20, "width": 200, "height": 30}
        preview = page.context.new_page.return_value
        with tempfile.TemporaryDirectory() as temp:
            def screenshot(**kwargs):
                Path(kwargs["path"]).write_bytes(b"synthetic image placeholder")
            preview.screenshot.side_effect = screenshot
            with patch("provadis_m1.failure_diagnostics.controls", return_value={"password": locator}):
                result = capture_failure(page, Path(temp), {"auth_state": "password"}, Exception)
            content = (Path(temp) / result["report"]).read_text()
            self.assertNotIn("SECRET", content)
            self.assertNotIn("TOKEN", content)
            self.assertEqual(json.loads(content)["controls"], {"password": 1})
            markup = preview.set_content.call_args.args[0]
            self.assertIn("password", markup)
            self.assertNotIn("SECRET", markup)
            self.assertEqual((Path(temp) / result["report"]).stat().st_mode & 0o777, 0o600)
        page.content.assert_not_called()
        page.screenshot.assert_not_called()

    def test_closed_browser_still_writes_json_report(self):
        class BrowserError(Exception):
            pass
        page = Mock(url="about:blank")
        page.context.new_page.side_effect = BrowserError("SECRET")
        with tempfile.TemporaryDirectory() as temp:
            with patch("provadis_m1.failure_diagnostics.controls", side_effect=BrowserError("SECRET")):
                result = capture_failure(page, Path(temp), {}, BrowserError)
            report = json.loads((Path(temp) / result["report"]).read_text())
        self.assertTrue(report["control_probe_incomplete"])
        self.assertTrue(report["control_layout_unavailable"])
        self.assertNotIn("SECRET", json.dumps(report))
