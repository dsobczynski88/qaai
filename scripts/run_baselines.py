#!/usr/bin/env python
"""Run the full test-suite (RTM) reviewer graph over a queue of baseline IDs, sequentially.

Each baseline is fetched from JAMA and reviewed by the RTM graph; every call mints its
own timestamped run folder (inputs.jsonl / outputs.jsonl / viewer.html / graph.png /
qaai.log / token_usage.jsonl). Folders land under ``logs/tests/`` by default so they stay
separate from production/API-server runs under ``logs/``.

Wiring mirrors qaai.api.main:lifespan (client, model_kwargs, pyjama_config, cache_manager)
so behaviour matches a real /api/v1/test-suite-review request.

Usage:
  uv run python scripts/run_baselines.py BASE-1 BASE-2 BASE-3
  uv run python scripts/run_baselines.py --file baselines.txt        # one id per line
  uv run python scripts/run_baselines.py BASE-1 --cache-mode off --edge-case
  uv run python scripts/run_baselines.py BASE-1 --base-dir ./logs     # production layout

Notes:
  * --cache-mode off (default here) re-runs every node and writes fresh outputs.
  * Needs the same .env the API server uses (API_KEY / API_BASE_URL / API_MODEL, JAMA_*).
  * One bad baseline does not stop the queue; a per-baseline summary prints at the end.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Point every start_new_run() at the chosen base dir BEFORE importing anything that
# builds the app (create_app runs at import time and reads settings.log_base_dir).
from qaai.core.config import settings


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("baselines", nargs="*", help="Baseline IDs to review, in order.")
    p.add_argument("--file", type=Path, help="Read baseline IDs from a file (one per line; '#' comments ok).")
    p.add_argument("--cache-mode", choices=["off", "on", "test"], default="off",
                   help="Graph cache mode (default: off — re-run all nodes, write fresh outputs).")
    p.add_argument("--edge-case", action="store_true",
                   help="Use the edge-case prompt set (test_suite_reviewer_v4) instead of the v3 baseline.")
    p.add_argument("--review-type", choices=["tests", "requirements"], default="tests",
                   help="JAMA fetch shape (see resolve_request_type). Default: tests.")
    p.add_argument("--base-dir", default="./logs/tests",
                   help="Base dir for run folders (default: ./logs/tests).")
    return p.parse_args(argv)


def _collect_baselines(args) -> list[str]:
    ids = list(args.baselines)
    if args.file:
        for line in args.file.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    return ids


def _build_rtm_service():
    """Construct an RTMReviewService the same way qaai.api.main:lifespan does."""
    from qaai.agents.clients import RateLimitOpenAIClient
    from qaai.core.cache import ReviewCacheManager
    from qaai.core.telemetry import TokenUsageTracker
    from qaai.api.main import build_pyjama_config
    from qaai.api.services import RTMReviewService

    telemetry_tracker = TokenUsageTracker(
        file_path=None,  # resolves settings.telemetry_file_path per write (re-pointed per run)
        input_cost_per_million=settings.token_cost_input_per_m,
        output_cost_per_million=settings.token_cost_output_per_m,
    )
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        base_url=settings.url,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
        telemetry_tracker=telemetry_tracker,
    )
    if settings.model in settings.models_using_max_completion_tokens:
        settings.model_kwargs.update({"max_completion_tokens": settings.max_output_tokens})
    else:
        settings.model_kwargs.update({"max_tokens": settings.max_output_tokens})

    cache_manager = None
    if settings.enable_cache:
        cache_manager = ReviewCacheManager(
            cache_dir=settings.cache_dir,
            redis_url=settings.redis_url,
            telemetry_tracker=telemetry_tracker,
        )
    return RTMReviewService(
        client,
        settings.model,
        model_kwargs=settings.model_kwargs,
        pyjama_config=build_pyjama_config(),
        cache_manager=cache_manager,
    )


async def _run(args) -> int:
    from qaai.api.services import resolve_prompt_set

    baselines = _collect_baselines(args)
    if not baselines:
        print("No baseline IDs given. Pass them as args or via --file.", file=sys.stderr)
        return 2

    service = _build_rtm_service()
    prompt_set = resolve_prompt_set(args.edge_case)

    print(f"Queue: {len(baselines)} baseline(s) | cache_mode={args.cache_mode} | "
          f"prompt_set={prompt_set} | base_dir={settings.log_base_dir}")

    results = []
    for i, baseline_id in enumerate(baselines, 1):
        print(f"\n[{i}/{len(baselines)}] {baseline_id} — starting")
        try:
            viewer = await service.run_from_baseline(
                baseline_id=baseline_id,
                thread_id_prefix=f"cli-{baseline_id}",
                cache_mode=args.cache_mode,
                prompt_set=prompt_set,
                baseline_review_type=args.review_type,
            )
            print(f"[{i}/{len(baselines)}] {baseline_id} — done: {viewer}")
            results.append((baseline_id, viewer, None))
        except Exception as exc:  # keep the queue going
            print(f"[{i}/{len(baselines)}] {baseline_id} — FAILED: {exc}", file=sys.stderr)
            results.append((baseline_id, None, str(exc)))

    print("\n=== Summary ===")
    for baseline_id, viewer, err in results:
        print(f"  {baseline_id}: {'OK  ' + viewer if viewer else 'FAIL ' + (err or '')}")
    return 0 if all(v for _, v, _ in results) else 1


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    settings.log_base_dir = args.base_dir
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
