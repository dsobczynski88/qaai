"""Editor HTML: self-contained, injection-safe, and driven by schema + spec."""

import json
import re

import pytest

from qaai.dataset_studio.editor import (
    EDITOR_TEMPLATE,
    build_config,
    build_editor_html,
    build_rows,
)
from qaai.dataset_studio.registry import DATASET_TYPES, dataset_type_for, load_type_spec

pytestmark = pytest.mark.unit


def _html(dtype="test_suite", rows=None, **kw):
    info = dataset_type_for(dtype)
    spec = load_type_spec(info)
    return build_editor_html(
        info=info, spec=spec, dataset_dir=f"eval/datasets/{dtype}/2026-07-19_10-00-00",
        rows=rows if rows is not None else build_rows([{}], [{}], [{}]),
        save_url="http://127.0.0.1:5000", token="tok-123", reviewer="tester", **kw,
    )


def _embedded(html, script_id):
    m = re.search(rf'<script id="{script_id}" type="application/json">(.*?)</script>',
                  html, re.S)
    assert m, f"no embedded {script_id}"
    return json.loads(m.group(1).replace("<\\/", "</"))


@pytest.mark.parametrize("dtype", sorted(DATASET_TYPES))
def test_every_type_renders_with_no_unsubstituted_placeholders(dtype):
    html = _html(dtype)
    assert "{{" not in html
    assert html.lstrip().startswith("<!doctype html>")


@pytest.mark.parametrize("dtype", sorted(DATASET_TYPES))
def test_page_is_self_contained(dtype):
    """No CDN, no external stylesheet, no build step."""
    html = _html(dtype)
    assert "<link" not in html
    assert "src=" not in html.split("<body>")[0]
    assert "http://" not in html.replace("http://127.0.0.1:5000", "")


def test_config_carries_the_rubric_from_the_spec():
    cfg = _embedded(_html("test_suite"), "CONFIG")
    assert cfg["codes"] == ["M1", "M2", "M3", "M4", "M5", "R6"]
    assert cfg["mandatory_codes"] == ["M1", "M2", "M3", "M4", "M5"]
    assert cfg["advisory_codes"] == ["R6"]
    assert cfg["na_allowed"] == ["M2", "M3", "R6"]
    assert cfg["na_counts_as_pass"] is True
    assert cfg["verdict_path"] == "synthesized_assessment.overall_verdict"


def test_hazard_config_has_seven_codes_and_excludes_r7():
    cfg = _embedded(_html("hazard"), "CONFIG")
    assert cfg["codes"] == ["H1", "H2", "H3", "H4", "H5", "H6", "R7"]
    assert "R7" not in cfg["mandatory_codes"]
    assert cfg["na_allowed"] == ["H5", "R7"]


def test_test_case_config_has_no_na():
    cfg = _embedded(_html("test_case"), "CONFIG")
    assert cfg["na_allowed"] == []
    assert cfg["na_counts_as_pass"] is False
    assert cfg["code_field"] == "id"  # objectives key on `id`, not `code`


def test_no_rubric_literals_in_the_javascript():
    """The rubric must reach the page as data. A code baked into the JS would drift."""
    js = EDITOR_TEMPLATE  # no CONFIG/DATA substituted yet
    for code in ("M1", "M4", "H6", "R7", "expected_result_support"):
        assert code not in js, f"{code} is hard-coded in the editor JS"


def test_input_schema_comes_from_the_projected_model():
    schema = _embedded(_html("test_suite"), "INPUT_SCHEMA")
    assert set(schema["properties"]) == {"requirement", "test_cases", "design_docs"}
    # Nested models arrive as $defs the form renderer dereferences.
    assert "TestCase" in schema["$defs"]
    assert "steps" in schema["$defs"]["TestCase"]["properties"]


def test_hazard_schema_exposes_the_trace_matrix():
    schema = _embedded(_html("hazard"), "INPUT_SCHEMA")
    assert set(schema["properties"]) == {"hazard"}
    assert "HazardTraceMatrix" in schema["$defs"]
    assert "requirements" in schema["$defs"]["HazardTraceMatrix"]["properties"]


def test_hazard_schema_uses_field_names_not_excel_aliases():
    """The form is keyed by field name, so edits write the key the rows already use.

    HazardRowFromExcel aliases every field to its Excel column header
    ("SHA ID Number" for hazard_id, …), and Pydantic's schema uses validation
    aliases by default. Keying the form on aliases would make each edit write
    `hazard["SHA ID Number"]` into a row that already stores `hazard_id` — two keys
    for one field, silently.
    """
    schema = _embedded(_html("hazard"), "INPUT_SCHEMA")
    row = schema["$defs"]["HazardRowWithTraceMatrix"]["properties"]
    assert "hazard_id" in row
    assert "SHA ID Number" not in row
    assert "software_related_causes" in row
    assert "S/W Related Cause(s)" not in row


def test_rows_ride_along_as_data():
    rows = build_rows(
        [{"requirement": {"req_id": "REQ-042", "text": "SHALL log out."}}],
        [{"synthesized_assessment": {"overall_verdict": "Yes"}}],
        [{"Overall_Verdict": "Yes"}],
    )
    data = _embedded(_html(rows=rows), "DATA")
    assert data[0]["index"] == 0
    assert data[0]["input"]["requirement"]["req_id"] == "REQ-042"
    assert data[0]["label"]["Overall_Verdict"] == "Yes"


def test_script_breakout_is_escaped():
    """LLM-authored text is untrusted; a </script> in the data must not close the tag."""
    rows = build_rows(
        [{"requirement": {"req_id": "R", "text": "</script><img src=x onerror=alert(1)>"}}],
        [{}], [{}],
    )
    html = _html(rows=rows)
    body = html.split('<script id="DATA"', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in body
    assert "<\\/script>" in body
    # …and it still round-trips to the original text.
    assert _embedded(html, "DATA")[0]["input"]["requirement"]["text"].startswith("</script>")


def test_dataset_dir_is_html_escaped_where_it_lands_in_markup():
    """{{SOURCE}}/{{TITLE}} interpolate into HTML, so they must be entity-escaped.

    The same value also appears inside the CONFIG JSON block, where it is inert
    (a <script type="application/json"> body is not executed) and only needs the
    </ breakout guard — asserted separately below.
    """
    info = dataset_type_for("test_suite")
    hostile = 'x"><img src=x onerror=alert(1)>'
    html = build_editor_html(
        info=info, spec=load_type_spec(info), dataset_dir=hostile,
        rows=build_rows([{}], [{}], [{}]),
    )

    markup = html.split('<script id="DATA"', 1)[0]
    assert "<img src=x" not in markup
    assert "&lt;img" in markup

    assert _embedded(html, "CONFIG")["dataset_dir"] == hostile


def test_hostile_dataset_dir_cannot_break_out_of_the_config_block():
    info = dataset_type_for("test_suite")
    html = build_editor_html(
        info=info, spec=load_type_spec(info),
        dataset_dir="x</script><script>alert(1)</script>",
        rows=build_rows([{}], [{}], [{}]),
    )
    block = html.split('<script id="CONFIG" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert "alert(1)" in block          # still present as inert text…
    assert "</script>" not in block     # …but the tag never closes early


def test_read_only_flag_reaches_the_page():
    assert _embedded(_html(read_only=True), "CONFIG")["read_only"] is True
    assert _embedded(_html(), "CONFIG")["read_only"] is False


def test_token_and_save_url_reach_the_page():
    cfg = _embedded(_html(), "CONFIG")
    assert cfg["token"] == "tok-123"
    assert cfg["save_url"] == "http://127.0.0.1:5000"


def test_helpers_are_defined_exactly_once():
    """Regression guard on the dom.js extraction from shared.js."""
    for fn in ("escapeHTML", "openModal", "closeModal", "readData"):
        assert len(re.findall(rf"function {fn}\(", EDITOR_TEMPLATE)) == 1


def test_editor_does_not_bundle_the_reviewer_feedback_pane():
    """The editor uses dom.js + editor.js; shared.js's rating pane must not leak in."""
    assert "renderRatings" not in EDITOR_TEMPLATE
    assert "initEditor" in EDITOR_TEMPLATE


def test_build_rows_pads_a_misaligned_dataset():
    """A misaligned set must open in the editor — that is where it gets fixed."""
    rows = build_rows([{"a": 1}, {"a": 2}], [{"b": 1}], [])
    assert len(rows) == 2
    assert rows[1]["output"] == {} and rows[1]["label"] == {}


def test_build_config_defaults_are_safe_without_a_server():
    cfg = build_config(dataset_type_for("test_suite"),
                       load_type_spec(dataset_type_for("test_suite")), "somewhere")
    assert cfg["save_url"] == "" and cfg["token"] == "" and cfg["read_only"] is False
