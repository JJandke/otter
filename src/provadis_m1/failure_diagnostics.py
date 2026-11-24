"""Private state report and screenshot of a safe UI projection, never raw HTML.

An input mask alone cannot protect usernames, QR challenges, inline images,
CSS backgrounds or an unexpected error page. Render only fixed control labels
at their measured positions in a separate document, with no page content copied.
"""

import html
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .auth_flow import controls
from .navigation_log import safe_url


def capture_failure(page: Any, directory: Path, diagnostics: dict, pw_error: type) -> dict:
    stem = "failure-" + uuid.uuid4().hex
    report = {"location": safe_url(page.url), **diagnostics, "controls": {}}
    boxes = []
    try:
        for name, locator in controls(page).items():
            visible = locator.filter(visible=True)
            count = visible.count()
            report["controls"][name] = count
            if count != 1:
                continue
            box = visible.bounding_box(timeout=1000)
            if box is not None:
                # Numeric geometry only, bounded to the diagnostic viewport.
                coords = {k: max(0, min(1400, int(box[k])))
                          for k in ("x", "y", "width", "height")}
                boxes.append((name, coords))
    except pw_error:
        report["control_probe_incomplete"] = True
    result = {"report": stem + ".json"}
    preview = None
    try:
        # No browser chrome, remote resources, page text, form values or original
        # pixels can enter this image. CSP prevents remote loads in the projection.
        preview = page.context.new_page()
        preview.set_viewport_size({"width": 1440, "height": 1000})
        markup = ('<!doctype html><meta http-equiv="Content-Security-Policy" '
                  'content="default-src \'none\'; style-src \'unsafe-inline\'">'
                  '<style>body{background:#f4f5f8;font:16px sans-serif}'
                  '.box{position:absolute;border:2px solid #3659a8;background:#dce6ff;'
                  'overflow:hidden;box-sizing:border-box}</style>'
                  '<h1>OTTER: sanitized control layout</h1><p>Page content omitted.</p>')
        for name, box in boxes:
            markup += (f'<div class="box" style="left:{box["x"]}px;top:{box["y"]}px;'
                       f'width:{max(100, box["width"])}px;height:{max(28, box["height"])}px">'
                       f'{html.escape(name)}</div>')
        preview.set_content(markup, timeout=3000)
        image_path = directory / (stem + ".png")
        preview.screenshot(path=str(image_path), timeout=3000)
        image_path.chmod(0o600)
        result["control_layout"] = image_path.name
    except pw_error:
        report["control_layout_unavailable"] = True
    finally:
        if preview is not None:
            try:
                preview.close()
            except pw_error:
                pass
    fd = os.open(directory / result["report"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    return result
