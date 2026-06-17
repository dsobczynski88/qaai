"""HTML template for the hazard-risk-reviewer batch-output viewer.

Renders HazardReviewState records produced by qaai.agents.hazard_risk_reviewer.
Mirrors the RTM viewer layout; data shape differs — one HazardRecord plus an H1-H5
rubric, with a coverage-analysis modal for per-requirement spec coverage.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from pathlib import Path

_COMMON = Path(__file__).parent / "common"
_HERE   = Path(__file__).parent / "hazard_reviewer"

_css  = (_COMMON / "base.css").read_text(encoding="utf-8") + (_HERE / "style.css").read_text(encoding="utf-8")
_js   = (_COMMON / "shared.js").read_text(encoding="utf-8") + "\n" + (_HERE / "script.js").read_text(encoding="utf-8")
_html = (_COMMON / "layout.html").read_text(encoding="utf-8")

HZ_HTML_TEMPLATE = (
    _html
    .replace("{{CSS}}",          _css)
    .replace("{{JS}}",           _js)
    .replace("{{HEADER_TITLE}}", "Hazard Risk Reviewer Output Viewer")
)
