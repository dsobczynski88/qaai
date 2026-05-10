import asyncio
import os
import pytest

from autoqa.components.test_case_reviewer.pipeline import TCReviewerRunnable
from autoqa.components.test_case_reviewer.core import (
    TCReviewState,
    Requirement,
    TestCase,
    TestCaseAssessment,
)
from autoqa.components.test_case_reviewer.nodes import load_default_review_objectives
from tests.helpers import load_jsonl, serialize_state


TC_INPUTS = load_jsonl("converted_PRJ01624_VL_1.1.000_tc_reviewer_format.jsonl")

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

    expected_overall = "Yes" if all(o.verdict == "Yes" for o in checklist) else "No"
    assert asmt.overall_verdict == expected_overall, (
        f"overall_verdict={asmt.overall_verdict!r} disagrees with AND-across-checklist "
        f"(expected {expected_overall!r}); "
        f"per-objective verdicts={[(o.id, o.verdict) for o in checklist]}"
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
async def test_tc_pipeline_parametrized_fanout(
    real_client, real_model, jsonl_recorders_tc
):
    """Fan-out variant of the prior parametrized form. Builds the
    TCReviewerRunnable once, dispatches every row in gold_dataset-tc.jsonl via
    asyncio.gather capped at MAX_CONCURRENT in-flight, re-orders results to
    input order before recording, then accumulates per-row hard-rule failures
    into a single pytest.fail summary.

    Each input row carries the designed-intent prediction
    (expected_overall_verdict, expected_partial_objectives, primary_failure);
    those are attached to the recorded output for post-run match-rate analysis.
    """
    record_input, record_output = jsonl_recorders_tc
    review_objectives = load_default_review_objectives()
    graph = TCReviewerRunnable(client=real_client, model=real_model)
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def run_one(idx: int, tc_input: dict):
        async with sem:
            test_case = TestCase(**tc_input["test_case"])
            requirements = [Requirement(**r) for r in tc_input["upstream_requirements"]]
            return idx, tc_input, await graph.graph.ainvoke(
                {
                    "test_case": test_case,
                    "requirements": requirements,
                    "review_objectives": review_objectives,
                }
            )

    completed = await asyncio.gather(
        *(run_one(i, tc_input) for i, tc_input in enumerate(TC_INPUTS)),
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
        msg = f"{n}/{len(TC_INPUTS)} rows failed"
        if fail_msgs:
            msg += "\nassertion-failures:\n" + "\n".join(fail_msgs)
        if exception_failures:
            msg += "\nexceptions:\n" + "\n".join(f"  {e!r}" for e in exception_failures)
        pytest.fail(msg)
