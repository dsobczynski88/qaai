"""HTML template for the batch-output viewer.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from pathlib import Path

_HERE = Path(__file__).parent / "test_suite_reviewer"

_css  = (_HERE / "style.css").read_text(encoding="utf-8")
_js   = (_HERE / "script.js").read_text(encoding="utf-8")
_html = (_HERE / "template.html").read_text(encoding="utf-8")

HTML_TEMPLATE = (
    _html
    .replace("{{CSS}}", _css)
    .replace("{{JS}}", _js)
)
