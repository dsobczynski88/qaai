"""Run the hazard_risk_reviewer pipeline against a JSONL dataset using a live LLM.

Inputs : a JSONL file where each line is a HazardRecord (the same shape consumed
         by tests/integration/test_hazard_pipeline.py).
Outputs: writes outputs.jsonl + viewer_hz.html into the active run directory
         (logs/run-<ts>/, derived from autoqa.core.config.settings.log_file_path)
         alongside hazard_graph.png. Per-record state JSON files are also dropped
         next to outputs.jsonl for manual inspection.

Run:
    OPENAI_API_KEY=... uv run python scripts/run_hazard_pipeline.py \\
        tests/fixtures/generated/hazard_dataset/inputs.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# Use the OS trust store for TLS verification. On Windows in particular this
# avoids "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
# certificate" when the certifi bundle doesn't recognise the cert chain to
# api.openai.com (e.g. behind a corporate proxy that injects its own root).
# Must run before any HTTP client (httpx / openai SDK) creates its SSL ctx.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from pydantic import BaseModel

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.hazard_risk_reviewer.core import HazardRecord
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from autoqa.core.config import settings
from autoqa.prj_logger import format_elapsed_time
from autoqa.viewer import write_viewer_hz


def _serialize(state: dict) -> dict:
    """Convert a HazardReviewState (with possibly Pydantic-wrapped fields) to plain JSON."""
    out: dict = {}
    for key, value in state.items():
        if isinstance(value, BaseModel):
            out[key] = value.model_dump()
        elif isinstance(value, list):
            out[key] = [
                v.model_dump() if isinstance(v, BaseModel) else v for v in value
            ]
        else:
            out[key] = value
    return out


def _load_hazards(jsonl_path: Path) -> list[HazardRecord]:
    records: list[HazardRecord] = []
    for i, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(HazardRecord.model_validate_json(line))
        except Exception as e:
            raise SystemExit(f"{jsonl_path}:{i}: failed to parse HazardRecord — {e}") from e
    return records


async def _run(jsonl_path: Path, model: str) -> Path:
    # pydantic-settings loads .env automatically, so settings.openai_api_key
    # is populated even when the OS env var is not exported.
    api_key = os.getenv("OPENAI_API_KEY") or settings.openai_api_key
    if not api_key or api_key == "your_api_key":
        raise SystemExit("OPENAI_API_KEY not set (.env or environment) — cannot run live pipeline")

    hazards = _load_hazards(jsonl_path)
    print(f"loaded {len(hazards)} hazard record(s) from {jsonl_path}")

    client = RateLimitOpenAIClient(api_key=api_key)
    graph = HazardReviewerRunnable(client=client, model=model)

    run_dir = Path(settings.log_file_path).parent
    outputs_path = run_dir / "outputs.jsonl"
    outputs_path.write_text("", encoding="utf-8")  # truncate

    # Track timing for each invocation
    invocation_times = []
    overall_start = time.perf_counter()
    logger = logging.getLogger("autoqa.hazard_pipeline")

    for i, hazard in enumerate(hazards, start=1):
        print(f"[{i}/{len(hazards)}] running {hazard.hazard_id} "
              f"({len(hazard.requirements)} req, {len(hazard.test_cases)} tc) ...")
        
        # Time this specific invocation
        start_time = time.perf_counter()
        result = await graph.graph.ainvoke({"hazard": hazard})
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        invocation_times.append((hazard.hazard_id, elapsed))
        
        # Log the timing for this invocation
        elapsed_str = format_elapsed_time(elapsed)
        print(f"  completed in {elapsed_str}")
        logger.info(f"Hazard {hazard.hazard_id} graph invocation completed in {elapsed_str}")
        
        serialised = _serialize(result)
        with outputs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(serialised, ensure_ascii=False) + "\n")

        per_record_path = run_dir / f"hazard_pipeline_state_{hazard.hazard_id}.json"
        per_record_path.write_text(json.dumps(serialised, indent=2), encoding="utf-8")

        ha = result.get("hazard_assessment")
        if ha is not None:
            print(f"  overall_verdict = {ha.overall_verdict}")
            for f in ha.mandatory_findings:
                print(f"    {f.code} {f.dimension}: {f.verdict} — {f.rationale}")
        else:
            print("  hazard_assessment = None (pipeline did not produce one)")

    # Calculate and log total time
    overall_end = time.perf_counter()
    total_elapsed = overall_end - overall_start
    total_str = format_elapsed_time(total_elapsed)
    
    # Print timing summary to console
    print(f"\n{'='*70}")
    print(f"TIMING SUMMARY")
    print(f"{'='*70}")
    for hazard_id, elapsed in invocation_times:
        print(f"  {hazard_id}: {format_elapsed_time(elapsed)}")
    print(f"{'='*70}")
    print(f"Total time across all {len(invocation_times)} invocations: {total_str}")
    print(f"{'='*70}\n")
    
    # Log timing summary to log file
    logger.info("="*70)
    logger.info("TIMING SUMMARY")
    logger.info("="*70)
    for hazard_id, elapsed in invocation_times:
        logger.info(f"  {hazard_id}: {format_elapsed_time(elapsed)}")
    logger.info("="*70)
    logger.info(f"Total time across all {len(invocation_times)} invocations: {total_str}")
    logger.info("="*70)

    viewer_path = write_viewer_hz(outputs_path)
    if viewer_path is not None:
        print(f"viewer rendered: {viewer_path}")
    print(f"outputs:        {outputs_path}")
    print(f"run directory:  {run_dir}")
    return run_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl_path", help="Path to inputs.jsonl (one HazardRecord per line)")
    ap.add_argument("--model", default=os.getenv("TEST_MODEL") or settings.model,
                    help="OpenAI model id (default: $TEST_MODEL or settings.model)")
    args = ap.parse_args(argv)

    jsonl_path = Path(args.jsonl_path)
    if not jsonl_path.exists():
        print(f"error: {jsonl_path} does not exist", file=sys.stderr)
        return 2

    try:
        asyncio.run(_run(jsonl_path, args.model))
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: pipeline run failed — {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())