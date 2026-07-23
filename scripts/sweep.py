"""Concurrent MLflow hyperparameter sweep over models × prompt sets (extensible).

Fans out one ``scripts/evaluate_with_mlflow.py`` process per grid cell (a "arm"),
then ranks the arms on their logged MLflow metrics. The sweep owns the three things
that make hand-running concurrent arms hazard-prone:

  1. Per-arm predictions dir  — a deterministic ``<dataset-dir>/predictions/<experiment>/<arm>``
     so two arms starting in the same wall-clock second never overwrite each other
     (``new_predictions_dir`` is only second-resolution with ``exist_ok=True``).
  2. Rate-limit division       — each child gets ``MAX_REQUESTS_PER_MINUTE`` divided by the
     number of simultaneous arms, so N arms don't blow past the endpoint's ceiling.
  3. Serial experiment create   — the parent calls ``set_experiment`` once before any child,
     so the ``file:./mlruns`` store never races on experiment creation.

The unit of work is the existing eval CLI — no harness logic is duplicated here.

Example:
    uv run python scripts/sweep.py \
        --spec eval/specs/test_suite_reviewer.yaml \
        --dataset-dir eval/datasets/test_suite/actual/pilot-20-record \
        --models gpt-5-mini,claude-sonnet-5 \
        --prompt-sets test_suite_reviewer_v3,test_suite_reviewer_v4 \
        --experiment rtm-sweep-2026-07-20 \
        --max-concurrent 4 --limit 20 --max-parallel-arms 4

Drill into a single arm afterwards:
    uv run python -m qaai.eval.compare <dataset-dir>/predictions/<experiment>/<arm>/

Caveats (this is plumbing, not a selection oracle):
  * The committed RTM pilot is 20 rows; per qaai/eval/sample_size.py, 95%/±0.05 needs
    385 (p=0.5) / 196 (p=0.85). At n=20 the CI on overall_f1 (~±0.20) is wider than any
    plausible arm gap — treat sweeps as smoke tests until the dataset grows.
  * Sweeping against an answer key whose labels don't match content optimizes for
    agreement with bad labels. Fix label quality before trusting a ranking.
"""
import argparse
import asyncio
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_CLI = REPO_ROOT / "scripts" / "evaluate_with_mlflow.py"


def _slug(text: str) -> str:
    """Filesystem/MLflow-safe arm fragment: replace path/namespace separators."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


@dataclass
class Arm:
    model: str
    prompt_set: str

    @property
    def name(self) -> str:
        return f"{_slug(self.model)}__{_slug(self.prompt_set)}"


def build_grid(models: List[str], prompt_sets: List[str]) -> List[Arm]:
    """Cartesian product model × prompt_set. Add axes here to extend the sweep."""
    return [Arm(m, ps) for m in models for ps in prompt_sets]


def _child_command(args: argparse.Namespace, arm: Arm, predictions_dir: Path) -> List[str]:
    cmd = [
        sys.executable, str(EVAL_CLI),
        "--spec", str(args.spec),
        "--dataset-dir", str(args.dataset_dir),
        "--mode", "run",
        "--model", arm.model,
        "--prompt-set", arm.prompt_set,
        "--experiment", args.experiment,
        "--run-name", arm.name,
        "--max-concurrent", str(args.max_concurrent),
        "--predictions-dir", str(predictions_dir),
        # The per-arm dir is already unique (<sweep_ts>/<arm>); skip the CLI's own <ts>/ subdir
        # so predictions land at predictions/<sweep_ts>/<arm>/ instead of a redundant double ts.
        "--no-timestamp-subdir",
        "--tracking-uri", args.tracking_uri,
    ]
    if args.limit is not None:
        cmd += ["--limit", str(args.limit)]
    if args.allow_prod:
        cmd += ["--allow-prod"]
    return cmd


def _child_env(arm: Arm, per_arm_rpm: int) -> dict:
    """Env captured by the child's settings singleton at import time.

    API_MODEL keeps telemetry cost-rate lookups and any settings-derived behavior aligned
    with the arm (belt-and-braces alongside --model); MAX_REQUESTS_PER_MINUTE divides the
    shared endpoint's ceiling across the simultaneous arms.
    """
    env = dict(os.environ)
    env["API_MODEL"] = arm.model
    env["MAX_REQUESTS_PER_MINUTE"] = str(per_arm_rpm)
    return env


def _run_arm(cmd: List[str], env: dict, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT, cwd=str(REPO_ROOT))
        return proc.wait()


def _resolve_rpm(n_concurrent: int) -> int:
    """floor(settings.max_requests_per_minute / n_concurrent), min 1."""
    from qaai.core.config import settings
    return max(1, math.floor(settings.max_requests_per_minute / max(1, n_concurrent)))


def preflight_models(models: List[str], allow_prod: bool) -> Dict[str, Optional[str]]:
    """Ping each unique model once against the configured endpoint.

    Every arm targets the single settings-derived endpoint (base_url/api_key), so a model
    id it doesn't serve (e.g. an Anthropic id on an OpenAI endpoint) 404s on every record and
    silently yields all-null. Catch that in ~1 cheap call. Returns {model: None if served else
    error-repr}. No token cap is sent — some models require max_completion_tokens vs max_tokens,
    and a 404 for an unserved model raises before any token param matters.
    """
    from qaai.eval import runners

    async def _check(client, model: str) -> Optional[str]:
        try:
            await client.chat_completion(model=model, messages=[{"role": "user", "content": "ping"}])
            return None
        except Exception as e:  # noqa: BLE001 — any failure means "don't spend a full arm on it"
            return repr(e)

    async def _run() -> Dict[str, Optional[str]]:
        client, _ = runners.build_client(allow_prod=allow_prod)
        return {m: await _check(client, m) for m in dict.fromkeys(models)}

    return asyncio.run(_run())


def _rank_and_report(experiment: str, tracking_uri: str) -> None:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    df = mlflow.search_runs(experiment_names=[experiment])
    if df is None or df.empty:
        print(f"[sweep] no MLflow runs found under experiment {experiment!r}.")
        return
    # Flag degenerate arms (every record failed / skipped) so they can't sit in the table
    # looking comparable to a real result.
    if "tags.all_records_failed" in df.columns:
        df["status"] = df["tags.all_records_failed"].map(
            lambda v: "FAILED" if str(v).lower() == "true" else "ok"
        )
    elif "metrics.skip_rate" in df.columns:
        df["status"] = df["metrics.skip_rate"].map(lambda v: "FAILED" if v == 1.0 else "ok")
    cols = [
        ("tags.mlflow.runName", "run"),
        ("status", "status"),
        ("params.model", "model"),
        ("params.prompt_set", "prompt_set"),
        ("metrics.overall_f1", "overall_f1"),
        ("metrics.rubric_macro_f1", "rubric_macro_f1"),
        ("metrics.exact_match_rate", "exact_match"),
        ("metrics.error_rate", "error_rate"),
        ("metrics.skip_rate", "skip_rate"),
        ("metrics.estimated_cost_usd", "cost_usd"),
    ]
    present = [(src, label) for src, label in cols if src in df.columns]
    view = df[[src for src, _ in present]].copy()
    view.columns = [label for _, label in present]
    if "overall_f1" in view.columns:
        view = view.sort_values("overall_f1", ascending=False, na_position="last")
    print("\n[sweep] ranking (by overall_f1 desc; FAILED = every record errored/skipped):")
    print(view.to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, required=True, help="Path to eval/specs/<name>.yaml")
    ap.add_argument("--dataset-dir", type=Path, required=True, help="Answer-key dataset directory")
    ap.add_argument("--models", required=True, help="Comma-separated model ids (the model axis)")
    ap.add_argument("--prompt-sets", required=True, help="Comma-separated prompt-set names (the prompt axis)")
    ap.add_argument("--experiment", required=True, help="MLflow experiment name (created once, up front)")
    ap.add_argument("--max-concurrent", type=int, default=10, help="Per-arm in-process graph concurrency")
    ap.add_argument("--limit", type=int, help="Only evaluate the first N records per arm")
    ap.add_argument("--max-parallel-arms", type=int, default=4, help="Cap on simultaneous child processes")
    ap.add_argument("--allow-prod", action="store_true", help="Permit a base_url containing 'prod'")
    ap.add_argument(
        "--tracking-uri",
        default=os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"),
        help="MLflow tracking URI (default file:./mlruns)",
    )
    ap.add_argument(
        "--skip-unavailable-models",
        action="store_true",
        help="Drop models the preflight can't reach instead of aborting the whole sweep",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print the plan (command + env + dir) and exit")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    prompt_sets = [p.strip() for p in args.prompt_sets.split(",") if p.strip()]

    # Preflight (Part 3): fail fast on a model id the endpoint doesn't serve, before spending
    # a full arm per (model x prompt_set) producing silent all-null output.
    status = preflight_models(models, allow_prod=args.allow_prod)
    served = [m for m in models if status[m] is None]
    unserved = {m: status[m] for m in models if status[m] is not None}
    print("[sweep] preflight:")
    for m in models:
        print(f"  {'OK  ' if status[m] is None else 'FAIL'} {m}"
              + (f"  -> {status[m]}" if status[m] else ""))
    if unserved:
        if args.skip_unavailable_models:
            print(f"[sweep] dropping unserved models: {', '.join(unserved)}")
            models = served
        else:
            print(f"[sweep] ABORT: {len(unserved)} model(s) not served by this endpoint. "
                  f"Pass --skip-unavailable-models to run the rest, or fix the model ids.")
            return 2
    if not models:
        print("[sweep] no served models remain — nothing to do.")
        return 2

    grid = build_grid(models, prompt_sets)
    if not grid:
        print("[sweep] empty grid — nothing to do.")
        return 1

    from qaai.core.logging_config import US_CENTRAL
    sweep_ts = datetime.now(tz=US_CENTRAL).strftime("%Y-%m-%d_%H-%M-%S")
    n_concurrent = min(args.max_parallel_arms, len(grid))
    per_arm_rpm = _resolve_rpm(n_concurrent)
    # One fresh timestamped predictions folder per sweep run; arms are collision-free subdirs.
    preds_root = args.dataset_dir / "predictions" / sweep_ts
    log_root = REPO_ROOT / "logs" / f"sweep-{_slug(args.experiment)}"

    plan = []
    for arm in grid:
        pred_dir = preds_root / arm.name
        cmd = _child_command(args, arm, pred_dir)
        env = _child_env(arm, per_arm_rpm)
        log_path = log_root / f"{arm.name}.log"
        plan.append((arm, cmd, env, pred_dir, log_path))

    print(f"[sweep] {len(grid)} arms, up to {n_concurrent} in parallel, "
          f"MAX_REQUESTS_PER_MINUTE={per_arm_rpm} per arm, experiment={args.experiment!r}")

    if args.dry_run:
        for arm, cmd, env, pred_dir, log_path in plan:
            print(f"\n[arm] {arm.name}")
            print(f"  API_MODEL={env['API_MODEL']}  MAX_REQUESTS_PER_MINUTE={env['MAX_REQUESTS_PER_MINUTE']}")
            print(f"  predictions-dir: {pred_dir}")
            print(f"  log: {log_path}")
            print(f"  cmd: {' '.join(cmd)}")
        return 0

    # Serial experiment pre-create (hazard 3): claim the experiment before any child races to.
    import mlflow
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(args.experiment)

    results = {}
    with ThreadPoolExecutor(max_workers=n_concurrent) as pool:
        futures = {
            pool.submit(_run_arm, cmd, env, log_path): (arm, log_path)
            for arm, cmd, env, _pred_dir, log_path in plan
        }
        for fut in futures:
            arm, log_path = futures[fut]
            try:
                results[arm.name] = (fut.result(), log_path)
            except Exception as exc:  # a spawn failure, not a child non-zero exit
                results[arm.name] = (-1, log_path)
                print(f"[sweep] arm {arm.name} failed to launch: {exc}")

    failed = {name: log for name, (rc, log) in results.items() if rc != 0}
    for name, log in failed.items():
        print(f"[sweep] FAILED arm {name} (exit {results[name][0]}) — see {log}")

    _rank_and_report(args.experiment, args.tracking_uri)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
