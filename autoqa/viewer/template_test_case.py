"""HTML template for the test-case-reviewer batch-output viewer.

Renders TCReviewState records produced by autoqa.components.test_case_reviewer.
Mirrors the RTM viewer layout; data shape differs — one TestCase + traced
requirements + a 5-row checklist plus a spec-axis coverage modal.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from pathlib import Path

_COMMON = Path(__file__).parent / "common"
_HERE   = Path(__file__).parent / "test_case_reviewer"

_css  = (_COMMON / "base.css").read_text(encoding="utf-8") + (_HERE / "style.css").read_text(encoding="utf-8")
_js   = (_HERE / "script.js").read_text(encoding="utf-8")
_html = (_COMMON / "layout.html").read_text(encoding="utf-8")

TC_HTML_TEMPLATE = (
    _html
    .replace("{{CSS}}",          _css)
    .replace("{{JS}}",           _js)
    .replace("{{HEADER_TITLE}}", "Single Test Case Reviewer")
)
