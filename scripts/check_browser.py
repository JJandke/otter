"""Launch an ephemeral sandboxed Chromium; no portal, API, or saved login state."""

import json
from pathlib import Path
import tempfile


def main() -> int:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({'ok': False, 'reason': 'playwright_not_installed'}))
        return 6
    try:
        with tempfile.TemporaryDirectory(prefix='otter-browser-smoke-') as directory:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(Path(directory) / 'profile'), headless=True, channel='chromium',
                    chromium_sandbox=True, accept_downloads=False, timeout=30000,
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_content('<title>OTTER M1 smoke</title>')
                    if page.title() != 'OTTER M1 smoke':
                        print(json.dumps({'ok': False, 'reason': 'browser_content_check_failed'}))
                        return 6
                finally:
                    context.close()
    except (PlaywrightError, OSError):
        # Browser errors may contain local paths and other machine-specific data.
        print(json.dumps({'ok': False, 'reason': 'browser_smoke_failed'}))
        return 6
    print(json.dumps({'ok': True, 'category': 'browser_smoke', 'sandbox_enabled': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
