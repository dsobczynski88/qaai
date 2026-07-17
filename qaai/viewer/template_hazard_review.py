"""HTML template for the hazard-risk-reviewer batch-output viewer.

Renders HazardReviewState records produced by qaai.agents.hazard_risk_reviewer.
Mirrors the RTM viewer layout; data shape differs — one HazardRecord plus an H1-H5
rubric, with a coverage-analysis modal for per-requirement spec coverage.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from qaai.viewer._loader import load_template

HZ_HTML_TEMPLATE = load_template("hazard_reviewer", "Hazard Risk Reviewer")
