"""Integration tests for the test_suite_reviewer pipeline.

Consolidated test suite that includes:
- Individual node tests (decomposer, summarizer, coverage evaluator)
- Full pipeline tests with parametrization for min/all fields
- Design document (R6) testing
"""
import asyncio
import json
import logging
import os
import pytest
from pathlib import Path

from autoqa.core.config import settings, PromptConfig
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.components.test_suite_reviewer.core import (
    RTMReviewState, Requirement, TestCase, DesignDocument, DecomposedRequirement, 
    TestSuite, EvaluatedSpec, SynthesizedAssessment,
)

from autoqa.prj_logger import format_elapsed_time
from tests.helpers import load_jsonl, serialize_state


# Cap on rows in flight at once. Tunable via AUTOQA_FANOUT_CONCURRENCY env var
# for bisection. The RateLimitOpenAIClient already enforces RPM/TPM ceilings
# internally, so this semaphore is a soft cap to bound memory and tail latency.
MAX_CONCURRENT = int(os.getenv("AUTOQA_FANOUT_CONCURRENCY", "5"))


def _assert_partial_invariants(sa: SynthesizedAssessment) -> None:
    """Validate partial verdict invariants for SynthesizedAssessment."""
    findings = sa.mandatory_findings
    assert len(findings) >= 5, f"expected at least 5 mandatory findings (M1-M5), got {len(findings)}"
    
    # Check M1-M5 are present
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

    # overall_verdict should be Yes only if all M1-M5 are Yes or N-A
    # R6 (if present) should NOT affect overall_verdict
    m1_m5_findings = [f for f in findings if f.code in ["M1", "M2", "M3", "M4", "M5"]]
    expected_overall = (
        "Yes" if all(f.verdict in ("Yes", "N-A") for f in m1_m5_findings) else "No"
    )
    assert sa.overall_verdict == expected_overall, (
        f"overall_verdict={sa.overall_verdict!r} disagrees with aggregation rule "
        f"(expected {expected_overall!r}); partial-Yes findings must NOT flip to No. "
        f"M1-M5 verdicts={[(f.code, f.verdict) for f in m1_m5_findings]}, "
        f"partials={[(f.code, f.partial) for f in m1_m5_findings]}"
    )

async def _fanout_pipeline(
    real_client,
    real_model,
    jsonl_recorders,
    fixture_name: str,
    *,
    prompt_config: PromptConfig | None = None,
) -> None:
    """Shared fan-out body for the RTM-pipeline batch tests.

    Builds the LangGraph runnable ONCE (graph compilation is non-trivial),
    dispatches every row in the specified fixture via `asyncio.gather` capped at
    MAX_CONCURRENT in-flight, re-orders results to input order, writes
    inputs/outputs to the JSONL fixture in input-order alignment, then
    accumulates per-row assertion failures into a single pytest.fail summary.

    Optionally accepts a PromptConfig override for prompt-version comparison
    runs.
    """
    record_input, record_output = jsonl_recorders
    
    # Load the fixture
    pipeline_inputs = load_jsonl(fixture_name)
    
    # Configure model_kwargs with max_tokens to handle large outputs (100+ test cases)
    # Haiku supports up to 16K output tokens; this ensures the summarizer can process
    # all test cases without truncation
    model_kwargs = {"max_tokens": settings.max_output_tokens}
    
    if prompt_config is None:
        graph = RTMReviewerRunnable(
            client=real_client, 
            model=real_model,
            model_kwargs=model_kwargs
        )
    else:
        graph = RTMReviewerRunnable(
            client=real_client, 
            model=real_model, 
            prompt_config=prompt_config,
            model_kwargs=model_kwargs
        )
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    logger = logging.getLogger("autoqa.test.pipeline")

    # Track timing for each invocation
    invocation_times = []
    overall_start = asyncio.get_event_loop().time()

    async def run_one(idx: int, row: dict):
        async with sem:
            requirement = Requirement(**row["requirement"])
            test_cases = [TestCase(**tc) for tc in row["test_cases"]]
            design_docs = None
            if "design_docs" in row and row["design_docs"]:
                design_docs = [DesignDocument(**dd) for dd in row["design_docs"]]
            
            # Time this specific invocation
            start_time = asyncio.get_event_loop().time()
            
            state = {"requirement": requirement, "test_cases": test_cases}
            if design_docs:
                state["design_docs"] = design_docs
            
            result = await graph.graph.ainvoke(state)
            
            end_time = asyncio.get_event_loop().time()
            elapsed = end_time - start_time
            
            return idx, row, result, elapsed

    completed = await asyncio.gather(
        *(run_one(i, row) for i, row in enumerate(pipeline_inputs)),
        return_exceptions=True,
    )

    # Re-align to input order so outputs.jsonl[i] still corresponds to pipeline_inputs[i]
    completed_sorted = sorted(
        [c for c in completed if not isinstance(c, Exception)],
        key=lambda c: c[0],
    )
    exception_failures = [c for c in completed if isinstance(c, Exception)]

    # Extract timing information and record outputs
    for item in completed_sorted:
        if len(item) == 4:
            idx, row, result, elapsed = item
            invocation_times.append((row["requirement"]["req_id"], elapsed))
            record_input(row)
            record_output(serialize_state(result))
        else:
            # Fallback for unexpected structure
            idx, row, result = item[:3]
            record_input(row)
            record_output(serialize_state(result))

    # Calculate total time
    overall_end = asyncio.get_event_loop().time()
    total_elapsed = overall_end - overall_start
    
    # Log timing summary
    logger.info("="*70)
    logger.info(f"TIMING SUMMARY - {fixture_name}")
    logger.info("="*70)
    for req_id, elapsed in invocation_times:
        logger.info(f"  {req_id}: {format_elapsed_time(elapsed)}")
    logger.info("="*70)
    logger.info(f"Total time across all {len(invocation_times)} async invocations: {format_elapsed_time(total_elapsed)}")
    logger.info("="*70)

    fail_msgs = []
    for item in completed_sorted:
        if len(item) >= 3:
            idx, row, result = item[0], item[1], item[2]
            try:
                assert isinstance(result.get("decomposed_requirement"), DecomposedRequirement)
                assert isinstance(result.get("test_suite"), TestSuite)
                evals = result.get("coverage_analysis", [])
                assert len(evals) > 0
                assert all(isinstance(e, EvaluatedSpec) for e in evals)
                assert isinstance(result.get("synthesized_assessment"), SynthesizedAssessment)
                _assert_partial_invariants(result["synthesized_assessment"])
            except AssertionError as e:
                fail_msgs.append(f"  {row['requirement']['req_id']}: {e}")

    if exception_failures or fail_msgs:
        n = len(exception_failures) + len(fail_msgs)
        msg = f"{n}/{len(pipeline_inputs)} rows failed"
        if fail_msgs:
            msg += "\nassertion-failures:\n" + "\n".join(fail_msgs)
        if exception_failures:
            msg += "\nexceptions:\n" + "\n".join(f"  {e!r}" for e in exception_failures)
        pytest.fail(msg)


@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_name",
    [
        "test_suite_review_all_fields.jsonl",
    ],
)
async def test_test_suite_reviewer(real_client, real_model, jsonl_recorders, fixture_name):
    """Main parametrized test for the test_suite_reviewer pipeline.
    
    Tests both:
    - Min fields: Requirements and test cases only (M1-M5)
    - All fields: Requirements, test cases, and design docs (M1-M5 + R6)
    
    Uses asyncio.gather over every row in the fixture, capped at MAX_CONCURRENT 
    in-flight via asyncio.Semaphore. Default PromptConfig (settings.prompt_config).
    """
    await _fanout_pipeline(real_client, real_model, jsonl_recorders, fixture_name)