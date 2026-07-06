"""Integration tests for the test_suite_reviewer pipeline.

Each row in the fixture JSONL becomes its own pytest item, identified by
req_id, so failures are attributed to a specific requirement and individual
rows can be re-run with -k <req_id>.
"""
import pytest

from qaai.core.config import settings
from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.agents.test_suite_reviewer.core import (
    Requirement, TestCase, DesignDocument, DecomposedRequirement,
    TestSuite, EvaluatedSpec, SynthesizedAssessment,
)
from tests.helpers import serialize_state


# The `row` argument is parametrized at collection time by pytest_generate_tests
# in tests/conftest.py, over the rows of the fixture selected via --input-file
# (default: test_suite_review_all_fields.jsonl).


def _assert_partial_invariants(sa: SynthesizedAssessment) -> None:
    """Validate partial-verdict invariants for a SynthesizedAssessment."""
    findings = sa.mandatory_findings
    assert len(findings) >= 5, f"expected at least 5 mandatory findings (M1-M5), got {len(findings)}"

    codes = [f.code for f in findings]
    for expected_code in ["M1", "M2", "M3", "M4", "M5"]:
        assert expected_code in codes, f"Missing mandatory finding {expected_code}"

    for f in findings:
        if f.verdict in ("No", "N-A"):
            assert f.partial is False, (
                f"{f.code}: partial must be False when verdict={f.verdict!r}, got True"
            )
        if f.partial:
            assert f.verdict == "Yes", (
                f"{f.code}: partial=True requires verdict='Yes', got {f.verdict!r}"
            )

    # overall_verdict should be Yes only if all M1-M5 are Yes or N-A.
    # R6 (if present) must NOT affect overall_verdict.
    m1_m5 = [f for f in findings if f.code in ("M1", "M2", "M3", "M4", "M5")]
    expected_overall = "Yes" if all(f.verdict in ("Yes", "N-A") for f in m1_m5) else "No"
    assert sa.overall_verdict == expected_overall, (
        f"overall_verdict={sa.overall_verdict!r} disagrees with aggregation rule "
        f"(expected {expected_overall!r}); partial-Yes findings must NOT flip to No. "
        f"M1-M5 verdicts={[(f.code, f.verdict) for f in m1_m5]}, "
        f"partials={[(f.code, f.partial) for f in m1_m5]}"
    )


@pytest.mark.integration
async def test_test_suite_reviewer(real_client, real_model, jsonl_recorders, test_run_dir, row):
    """Run the full RTM pipeline for one fixture row against a real LLM.

    Parametrized (in conftest's pytest_generate_tests) over every row in the
    selected fixture (default test_suite_review_all_fields.jsonl, overridable
    with --input-file) so each requirement is its own pytest item — failures are
    attributed to a specific req_id and rows can be re-run individually with
    -k <req_id>.

    Tests the full field set: requirement + test cases + design docs (M1-M5 + R6).
    """
    record_input, record_output = jsonl_recorders

    # Deserialize the fixture row into typed objects.
    requirement = Requirement(**row["requirement"])
    test_cases = [TestCase(**tc) for tc in row["test_cases"]]
    design_docs = (
        [DesignDocument(**dd) for dd in row["design_docs"]]
        if row.get("design_docs") else None
    )

    # Build the graph. Compilation is non-trivial but amortized against the
    # LLM call time (~30-40s) that follows.
    if real_model in settings.models_using_max_completion_tokens:
        model_kwargs={"max_completion_tokens": settings.max_output_tokens}
    else:
        model_kwargs={"max_tokens": settings.max_output_tokens}

    graph = RTMReviewerRunnable(
        client=real_client,
        model=real_model,
        model_kwargs=model_kwargs,
    )
    # Mirror the API: drop the graph diagram into the per-session run folder.
    graph.write_graph_png(test_run_dir)

    state = {"requirement": requirement, "test_cases": test_cases}
    if design_docs:
        state["design_docs"] = design_docs

    record_input(row)
    result = await graph.graph.ainvoke(state)
    record_output(serialize_state(result))
    req_id = row["requirement"]["req_id"]
    evals = result.get("coverage_analysis", [])

    assert isinstance(result.get("decomposed_requirement"), DecomposedRequirement), \
        f"{req_id}: decomposed_requirement missing or wrong type"
    
    assert isinstance(result.get("test_suite"), TestSuite), \
        f"{req_id}: test_suite missing or wrong type" 
    
    assert len(evals) > 0, f"{req_id}: coverage_analysis is empty"
    
    assert all(isinstance(e, EvaluatedSpec) for e in evals), \
        f"{req_id}: coverage_analysis contains unexpected types"
    
    assert isinstance(result.get("synthesized_assessment"), SynthesizedAssessment), \
        f"{req_id}: synthesized_assessment missing or wrong type"
    
    _assert_partial_invariants(result["synthesized_assessment"])