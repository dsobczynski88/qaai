"""HTML viewer generator for RTM, test-case, and hazard-reviewer batch outputs.

Public API:
    from autoqa.viewer import write_viewer, write_viewer_tc, write_viewer_hz
    write_viewer("logs/run-<ts>/outputs.jsonl")        # RTM (test_suite_reviewer)
    write_viewer_tc("logs/run-<ts>/outputs.jsonl")     # test_case_reviewer
    write_viewer_hz("logs/run-<ts>/outputs.jsonl")     # hazard_risk_reviewer

CLI:
    uv run python -m autoqa.viewer logs/run-<ts>/outputs.jsonl [-o viewer.html] [--type rtm|tc|hz]
"""

from autoqa.viewer.generator import (
    build_viewer,
    build_viewer_hz,
    build_viewer_tc,
    write_viewer,
    write_viewer_hz,
    write_viewer_tc,
)
from autoqa.viewer.template import HTML_TEMPLATE
from autoqa.viewer.template_hazard_review import HZ_HTML_TEMPLATE
from autoqa.viewer.template_test_case import TC_HTML_TEMPLATE

__all__ = [
    "build_viewer",
    "build_viewer_hz",
    "build_viewer_tc",
    "write_viewer",
    "write_viewer_hz",
    "write_viewer_tc",
    "HTML_TEMPLATE",
    "HZ_HTML_TEMPLATE",
    "TC_HTML_TEMPLATE",
]
