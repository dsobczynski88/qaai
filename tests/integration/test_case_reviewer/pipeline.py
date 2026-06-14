"""Integration tests for the test_case_reviewer pipeline.

Each row in the fixture JSONL becomes its own pytest item, identified by
test_id, so failures are attributed to a specific test case and individual
rows can be re-run with -k <test_id>.
"""
import pytest

from qaai.components.test_case_reviewer.pipeline import TCReviewerRunnable
from qaai.components.test_case_reviewer.core import (
    Requirement,
    TestCase,
    DesignDocument,
    TestCaseAssessment,
)
from qaai.components.test_case_reviewer.nodes import load_default_review_objectives
from tests.helpers import serialize_state


# The `row` argument is parametrized at collection time by pytest_generate_tests
# in tests/conftest.py, over the rows of the fixture selected via --input-file
# (default: test_case_review_all_fields.jsonl).
_REVIEW_OBJECTIVES = load_default_review_objectives()
REVIEW_OBJECTIVE_IDS = {o.id for o in _REVIEW_OBJECTIVES}


def _assert_tc_verdict_invariants(asmt: TestCaseAssessment, state: dict) -> None:
    checklist = asmt.evaluated_checklist
    assert len(checklist) == 5, f"expected 5 checklist items, got {len(checklist)}"
    assert {o.id for o in checklist} == REVIEW_OBJECTIVE_IDS, (
        f"checklist ids {sorted(o.id for o in checklist)} != "
        f"review_objectives.yaml ids {sorted(REVIEW_OBJECTIVE_IDS)}"
    )

    for o in checklist:
        if o.verdict == "No":
            assert o.partial is False, (
                f"{o.id}: partial must be False when verdict='No', got True"
            )
        if o.partial:
            assert o.verdict == "Yes", (
                f"{o.id}: partial=True requires verdict='Yes', got {o.verdict!r}"
            )
        assert hasattr(o, "mandatory"), f"{o.id}: missing mandatory field"

    # overall_verdict is computed from MANDATORY criteria only.
    mandatory_checklist = [o for o in checklist if o.mandatory is not False]
    expected_overall = "Yes" if all(o.verdict == "Yes" for o in mandatory_checklist) else "No"
    assert asmt.overall_verdict == expected_overall, (
        f"overall_verdict={asmt.overall_verdict!r} disagrees with AND-across-MANDATORY-checklist "
        f"(expected {expected_overall!r}); "
        f"mandatory verdicts={[(o.id, o.verdict, o.mandatory) for o in mandatory_checklist]}; "
        f"all verdicts={[(o.id, o.verdict, o.mandatory) for o in checklist]}"
    )

    cov = {a.spec_id: a.exists for a in state.get("coverage_analysis", [])}
    n_total = len(cov)
    n_covered = sum(1 for v in cov.values() if v is True)
    if n_total == 0:
        expected_sa_verdict, expected_sa_partial = "No", False
    elif n_covered == n_total:
        expected_sa_verdict, expected_sa_partial = "Yes", False
    elif n_covered >= 1:
        expected_sa_verdict, expected_sa_partial = "Yes", True
    else:
        expected_sa_verdict, expected_sa_partial = "No", False

    spec_align = next(
        (o for o in checklist if o.id == "expected_result_spec_align"), None
    )
    assert spec_align is not None, "expected_result_spec_align missing from checklist"
    assert spec_align.verdict == expected_sa_verdict, (
        f"expected_result_spec_align.verdict={spec_align.verdict!r} disagrees with "
        f"count-based tier rule (expected {expected_sa_verdict!r}); "
        f"n_covered={n_covered}/{n_total}, coverage_exists={cov}"
    )
    assert spec_align.partial == expected_sa_partial, (
        f"expected_result_spec_align.partial={spec_align.partial} disagrees with "
        f"count-based tier rule (expected partial={expected_sa_partial}); "
        f"n_covered={n_covered}/{n_total}"
    )


@pytest.mark.integration
async def test_test_case_reviewer(real_client, real_model, jsonl_recorders_tc, row):
    """Run the full test-case-reviewer pipeline for one fixture row against a real LLM.

    Parametrized (in conftest's pytest_generate_tests) over every row in the
    selected fixture (default test_case_review_all_fields.jsonl, overridable with
    --input-file) so each test case is its own pytest item — failures are
    attributed to a specific test_id and rows can be re-run individually with
    -k <test_id>.

    Each output record is annotated with the row's designed-intent predictions
    (expected_overall_verdict, expected_partial_objectives, primary_failure) for
    post-run match-rate analysis.
    """
    record_input, record_output = jsonl_recorders_tc

    # Deserialize the fixture row into typed objects.
    # Support both 'upstream_requirements' and 'requirements' keys for backwards compatibility.
    test_case = TestCase(**row["test_case"])
    req_key = "upstream_requirements" if "upstream_requirements" in row else "requirements"
    requirements = [Requirement(**r) for r in row[req_key]]
    design_docs = (
        [DesignDocument(**dd) for dd in row["design_docs"]]
        if row.get("design_docs") else None
    )

    graph = TCReviewerRunnable(client=real_client, model=real_model)
    state = {
        "test_case": test_case,
        "requirements": requirements,
        "review_objectives": _REVIEW_OBJECTIVES,
    }
    if design_docs:
        state["design_docs"] = design_docs

    record_input(row)
    result = await graph.graph.ainvoke(state)

    # Annotate output with designed-intent predictions before recording.
    out = serialize_state(result)
    out["expected_overall_verdict"] = row.get("expected_overall_verdict")
    out["expected_partial_objectives"] = row.get("expected_partial_objectives", [])
    out["primary_failure"] = row.get("primary_failure")
    out["fixture_description"] = row.get("description")
    record_output(out)

    tc_id = row["test_case"]["test_id"]
    asmt = result.get("aggregated_assessment")
    assert isinstance(asmt, TestCaseAssessment), (
        f"{tc_id}: aggregated_assessment is {type(asmt).__name__}, not TestCaseAssessment "
        "(aggregator likely skipped due to upstream parse failures)"
    )
    _assert_tc_verdict_invariants(asmt, result)
