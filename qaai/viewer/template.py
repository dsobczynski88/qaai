"""HTML template for the batch-output viewer.

Renders RTMReviewState records produced by qaai.agents.test_suite_reviewer — one
Requirement plus its traced test suite and the M1-M5 + R6 rubric.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

from qaai.viewer._loader import load_template

HTML_TEMPLATE = load_template("test_suite_reviewer", "Test Suite Reviewer")
