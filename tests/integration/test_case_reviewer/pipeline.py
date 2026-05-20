"""Integration tests for the test_case_reviewer pipeline.

Consolidated test suite that supports both min and all fields fixtures.
"""
import asyncio
import os
import pytest

from autoqa.components.test_case_reviewer.pipeline import TCReviewerRunnable
from autoqa.components.test_case_reviewer.core import (
    TCReviewState,
    Requirement,
    TestCase,
    DesignDocument,
    TestCaseAssessment,
)
from autoqa.components.test_case_reviewer.nodes import load_default_review_objectives
from tests.helpers import load_jsonl, serialize_state

REVIEW_OBJECTIVE_IDS = {o.id for o in load_default_review_objectives()}

# Cap on rows in flight at once. Tunable via AUTOQA_FANOUT_CONCURRENCY env var
# for bisection. The RateLimitOpenAIClient already enforces RPM/TPM ceilings
# internally, so this semaphore is a soft cap to bound memory and tail latency.
MAX_CONCURRENT = int(os.getenv("AUTOQA_FANOUT_CONCURRENCY", "10"))


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
        # Verify mandatory field is present
        assert hasattr(o, "mandatory"), f"{o.id}: missing mandatory field"

    # overall_verdict should be computed from MANDATORY criteria only
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
@pytest.mark.parametrize(
    "fixture_name",
    [
        "test_case_review_all_fields.jsonl",
    ],
)
async def test_test_case_reviewer(
    real_client, real_model, jsonl_recorders_tc, fixture_name
):
    """Main parametrized test for the test_case_reviewer pipeline.
    
    Tests both:
    - Min fields: Test case and upstream requirements only
    - All fields: Test case, upstream requirements, and design docs
    
    Builds the TCReviewerRunnable once, dispatches every row in the fixture via
    asyncio.gather capped at MAX_CONCURRENT in-flight, re-orders results to
    input order before recording, then accumulates per-row hard-rule failures
    into a single pytest.fail summary.

    Each input row carries the designed-intent prediction
    (expected_overall_verdict, expected_partial_objectives, primary_failure);
    those are attached to the recorded output for post-run match-rate analysis.
    """
    # Load the fixture
    tc_inputs = load_jsonl(fixture_name)
    
    record_input, record_output = jsonl_recorders_tc
    review_objectives = load_default_review_objectives()
    graph = TCReviewerRunnable(client=real_client, model=real_model)
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def run_one(idx: int, tc_input: dict):
        async with sem:
            test_case = TestCase(**tc_input["test_case"])
            # Handle both 'requirements' and 'upstream_requirements' keys for backwards compatibility
            req_key = "upstream_requirements" if "upstream_requirements" in tc_input else "requirements"
            requirements = [Requirement(**r) for r in tc_input[req_key]]
            
            # Handle optional design_docs field
            design_docs = None
            if "design_docs" in tc_input and tc_input["design_docs"]:
                design_docs = [DesignDocument(**dd) for dd in tc_input["design_docs"]]
            
            state = {
                "test_case": test_case,
                "requirements": requirements,
                "review_objectives": review_objectives,
            }
            if design_docs:
                state["design_docs"] = design_docs
            
            return idx, tc_input, await graph.graph.ainvoke(state)

    completed = await asyncio.gather(
        *(run_one(i, tc_input) for i, tc_input in enumerate(tc_inputs)),
        return_exceptions=True,
    )

    completed_sorted = sorted(
        [c for c in completed if not isinstance(c, Exception)],
        key=lambda c: c[0],
    )
    exception_failures = [c for c in completed if isinstance(c, Exception)]

    # Write inputs and outputs in input order so line-N alignment is preserved.
    for _idx, tc_input, result in completed_sorted:
        record_input(tc_input)
        out = serialize_state(result)
        out["expected_overall_verdict"] = tc_input.get("expected_overall_verdict")
        out["expected_partial_objectives"] = tc_input.get("expected_partial_objectives", [])
        out["primary_failure"] = tc_input.get("primary_failure")
        out["fixture_description"] = tc_input.get("description")
        record_output(out)

    fail_msgs = []
    for _idx, tc_input, result in completed_sorted:
        tc_id = tc_input["test_case"]["test_id"]
        try:
            asmt = result.get("aggregated_assessment")
            assert isinstance(asmt, TestCaseAssessment), (
                f"aggregated_assessment is {type(asmt).__name__}, not TestCaseAssessment "
                "(aggregator likely skipped due to upstream parse failures)"
            )
            _assert_tc_verdict_invariants(asmt, result)
        except AssertionError as e:
            fail_msgs.append(f"  {tc_id}: {e}")

    if exception_failures or fail_msgs:
        n = len(exception_failures) + len(fail_msgs)
        msg = f"{n}/{len(tc_inputs)} rows failed (fixture: {fixture_name})"
        if fail_msgs:
            msg += "\nassertion-failures:\n" + "\n".join(fail_msgs)
        if exception_failures:
            msg += "\nexceptions:\n" + "\n".join(f"  {e!r}" for e in exception_failures)
        pytest.fail(msg)
