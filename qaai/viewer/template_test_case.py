"""HTML template for the test-case-reviewer batch-output viewer.

Renders TCReviewState records produced by qaai.agents.test_case_reviewer.
Mirrors the RTM viewer layout; data shape differs — one TestCase + traced
requirements + a 5-row checklist plus a spec-axis coverage modal.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from qaai.viewer._loader import load_template

TC_HTML_TEMPLATE = load_template("test_case_reviewer", "Single Test Case Reviewer")
