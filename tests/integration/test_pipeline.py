import asyncio
import json
import os
import pytest
from pathlib import Path
from autoqa.core.config import settings, PromptConfig
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.components.test_suite_reviewer.core import (
    RTMReviewState, Requirement, TestCase, DecomposedRequirement, TestSuite,
    EvaluatedSpec, SynthesizedAssessment,
)
from tests.helpers import load_jsonl, serialize_state

PIPELINE_INPUTS = load_jsonl("gold_dataset.jsonl")

# Cap on rows in flight at once. Tunable via AUTOQA_FANOUT_CONCURRENCY env var
# for bisection. The RateLimitOpenAIClient already enforces RPM/TPM ceilings
# internally, so this semaphore is a soft cap to bound memory and tail latency.
MAX_CONCURRENT = int(os.getenv("AUTOQA_FANOUT_CONCURRENCY", "10"))


def _assert_partial_invariants(sa: SynthesizedAssessment) -> None:
    findings = sa.mandatory_findings
    assert len(findings) == 5, f"expected 5 mandatory findings, got {len(findings)}"
    assert [f.code for f in findings] == ["M1", "M2", "M3", "M4", "M5"]

    for f in findings:
        if f.verdict in ("No", "N-A"):
            assert f.partial is False, (
                f"{f.code}: partial must be False when verdict={f.verdict!r}, got True"
            )
        if f.partial:
            assert f.verdict == "Yes", (
                f"{f.code}: partial=True requires verdict='Yes', got {f.verdict!r}"
            )

    expected_overall = (
        "Yes" if all(f.verdict in ("Yes", "N-A") for f in findings) else "No"
    )
    assert sa.overall_verdict == expected_overall, (
        f"overall_verdict={sa.overall_verdict!r} disagrees with aggregation rule "
        f"(expected {expected_overall!r}); partial-Yes findings must NOT flip to No. "
        f"verdicts={[f.verdict for f in findings]}, "
        f"partials={[f.partial for f in findings]}"
    )


async def _fanout_pipeline(
    real_client,
    real_model,
    jsonl_recorders,
    *,
    prompt_config: PromptConfig | None = None,
) -> None:
    """Shared fan-out body for the three RTM-pipeline batch tests.

    Builds the LangGraph runnable ONCE (graph compilation is non-trivial),
    dispatches every row in PIPELINE_INPUTS via `asyncio.gather` capped at
    MAX_CONCURRENT in-flight, re-orders results to input order, writes
    inputs/outputs to the JSONL fixture in input-order alignment, then
    accumulates per-row assertion failures into a single pytest.fail summary.

    Optionally accepts a PromptConfig override for prompt-version comparison
    runs (mirrors the prior _standard_coverage / _advanced_coverage variants).
    """
    record_input, record_output = jsonl_recorders
    if prompt_config is None:
        graph = RTMReviewerRunnable(client=real_client, model=real_model)
    else:
        graph = RTMReviewerRunnable(
            client=real_client, model=real_model, prompt_config=prompt_config
        )
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def run_one(idx: int, row: dict):
        async with sem:
            requirement = Requirement(**row["requirement"])
            test_cases = [TestCase(**tc) for tc in row["test_cases"]]
            return idx, row, await graph.graph.ainvoke(
                {"requirement": requirement, "test_cases": test_cases}
            )

    completed = await asyncio.gather(
        *(run_one(i, row) for i, row in enumerate(PIPELINE_INPUTS)),
        return_exceptions=True,
    )

    # Re-align to input order so outputs.jsonl[i] still corresponds to PIPELINE_INPUTS[i]
    completed_sorted = sorted(
        [c for c in completed if not isinstance(c, Exception)],
        key=lambda c: c[0],
    )
    exception_failures = [c for c in completed if isinstance(c, Exception)]

    for _idx, row, result in completed_sorted:
        record_input(row)
        record_output(serialize_state(result))

    fail_msgs = []
    for _idx, row, result in completed_sorted:
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
        msg = f"{n}/{len(PIPELINE_INPUTS)} rows failed"
        if fail_msgs:
            msg += "\nassertion-failures:\n" + "\n".join(fail_msgs)
        if exception_failures:
            msg += "\nexceptions:\n" + "\n".join(f"  {e!r}" for e in exception_failures)
        pytest.fail(msg)


@pytest.mark.integration
async def test_pipeline_decomposer_node(real_client, real_model, sample_requirement, sample_test_cases):
    """Run the full pipeline and verify the decomposer produced structured output."""
    graph = RTMReviewerRunnable(client=real_client, model=real_model)
    state = {"requirement": sample_requirement, "test_cases": sample_test_cases}
    result = await graph.graph.ainvoke(state)

    assert result.get("decomposed_requirement") is not None
    assert isinstance(result["decomposed_requirement"], DecomposedRequirement)
    specs = result["decomposed_requirement"].decomposed_specifications
    assert len(specs) > 0
    print(f"\n[decomposer] {len(specs)} specs generated")
    for s in specs:
        print(f"  {s.spec_id}: {s.description[:60]}")


@pytest.mark.integration
async def test_pipeline_summarizer_node(real_client, real_model, sample_requirement, sample_test_cases):
    """Verify summarizer produced a structured TestSuite."""
    graph = RTMReviewerRunnable(client=real_client, model=real_model)
    state = {"requirement": sample_requirement, "test_cases": sample_test_cases}
    result = await graph.graph.ainvoke(state)

    assert result.get("test_suite") is not None
    assert isinstance(result["test_suite"], TestSuite)
    assert len(result["test_suite"].summary) > 0
    print(f"\n[summarizer] {len(result['test_suite'].summary)} summaries produced")


@pytest.mark.integration
async def test_pipeline_coverage_node(real_client, real_model, sample_requirement, sample_test_cases):
    """Verify coverage evaluator produced EvaluatedSpec results."""
    graph = RTMReviewerRunnable(client=real_client, model=real_model)
    state = {"requirement": sample_requirement, "test_cases": sample_test_cases}
    result = await graph.graph.ainvoke(state)

    evals = result.get("coverage_analysis", [])
    assert len(evals) > 0
    assert all(isinstance(e, EvaluatedSpec) for e in evals)
    print(f"\n[coverage] {len(evals)} specs evaluated")
    for e in evals:
        dims = sorted({d for ctc in e.covered_by_test_cases for d in ctc.dimensions})
        print(f"  {e.spec_id}: covered={e.covered_exists}, dimensions={dims}")
        for ctc in e.covered_by_test_cases:
            print(f"    {ctc.test_case_id} ({','.join(ctc.dimensions)}): {ctc.rationale[:80]}")


@pytest.mark.integration
async def test_pipeline_full_state(real_client, real_model, sample_requirement, sample_test_cases):
    """Run the full pipeline, validate all RTMReviewState fields, and save state as JSON."""
    graph = RTMReviewerRunnable(client=real_client, model=real_model)
    initial_state = {"requirement": sample_requirement, "test_cases": sample_test_cases}
    result: RTMReviewState = await graph.graph.ainvoke(initial_state)

    assert isinstance(result.get("requirement"), Requirement)
    assert isinstance(result.get("test_cases"), list) and len(result["test_cases"]) > 0
    assert isinstance(result.get("decomposed_requirement"), DecomposedRequirement)
    assert len(result["decomposed_requirement"].decomposed_specifications) > 0
    assert isinstance(result.get("test_suite"), TestSuite)
    assert len(result["test_suite"].summary) > 0
    evals = result.get("coverage_analysis", [])
    assert len(evals) == len(result["decomposed_requirement"].decomposed_specifications)
    assert all(isinstance(e, EvaluatedSpec) for e in evals)

    output_path = Path(settings.log_file_path).parent / "pipeline_state.json"
    output_path.write_text(json.dumps(serialize_state(result), indent=2))
    print(f"\n[full_state] saved → {output_path}")


@pytest.mark.integration
async def test_pipeline_parametrized_fanout(real_client, real_model, jsonl_recorders):
    """Fan-out variant of the prior parametrized form. Uses asyncio.gather over
    every row in gold_dataset.jsonl, capped at MAX_CONCURRENT in-flight via
    asyncio.Semaphore. Default PromptConfig (settings.prompt_config)."""
    await _fanout_pipeline(real_client, real_model, jsonl_recorders)


@pytest.mark.integration
async def test_pipeline_parametrized_standard_coverage_fanout(
    real_client, real_model, jsonl_recorders
):
    """Fan-out variant pinning the 'standard coverage' prompt versions
    (decomposer-v4, summarizer-v2, coverage_evaluator-v6, synthesizer-v6)."""
    custom = PromptConfig(
        decomposer="decomposer-v4.jinja2",
        summarizer="summarizer-v2.jinja2",
        coverage="coverage_evaluator-v6.jinja2",
        synthesizer="synthesizer-v6.jinja2",
    )
    await _fanout_pipeline(real_client, real_model, jsonl_recorders, prompt_config=custom)


@pytest.mark.integration
async def test_pipeline_parametrized_advanced_coverage_fanout(
    real_client, real_model, jsonl_recorders
):
    """Fan-out variant pinning the older 'advanced coverage' prompt versions
    (decomposer-v3, summarizer-v2, coverage_evaluator-v4, synthesizer-v2)."""
    custom = PromptConfig(
        decomposer="decomposer-v3.jinja2",
        summarizer="summarizer-v2.jinja2",
        coverage="coverage_evaluator-v4.jinja2",
        synthesizer="synthesizer-v2.jinja2",
    )
    await _fanout_pipeline(real_client, real_model, jsonl_recorders, prompt_config=custom)
