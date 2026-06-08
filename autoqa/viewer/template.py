"""HTML template for the batch-output viewer.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from pathlib import Path

_COMMON = Path(__file__).parent / "common"
_HERE   = Path(__file__).parent / "test_suite_reviewer"

_css  = (_COMMON / "base.css").read_text(encoding="utf-8") + (_HERE / "style.css").read_text(encoding="utf-8")
_js   = (_COMMON / "shared.js").read_text(encoding="utf-8") + "\n" + (_HERE / "script.js").read_text(encoding="utf-8")
_html = (_COMMON / "layout.html").read_text(encoding="utf-8")

HTML_TEMPLATE = (
    _html
    .replace("{{CSS}}",          _css)
    .replace("{{JS}}",           _js)
    .replace("{{HEADER_TITLE}}", "Test Suite Reviewer")
)