"""Render a record-by-record actual-vs-predicted diff for one evaluation run.

Given a ``predictions/<ts>/`` folder produced by ``--mode run`` (see
``qaai/eval/harness.py::_write_prediction_set``), this loads the parent answer-key
dataset, aligns it row-by-row with the run's predictions, and builds merged comparison
records that the ``eval_compare`` viewer renders into a single self-contained
``compare.html``: per record the graph inputs, the actual vs predicted overall verdict +
rubric cells (deviations highlighted), and a raw ``actual_output`` vs ``predicted_output``
drill-down.

Schema-agnostic: every cell is read through the run's :class:`~qaai.eval.spec.EvalSpec`
(auto-resolved from ``run_metadata.json``), so the same viewer works for the RTM, hazard,
and test-case reviewers with no code change.

CLI::

    python -m qaai.eval.compare eval/datasets/test_suite/predictions/<ts>/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from qaai.eval.datasets import (
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    METADATA_NAME,
    PREDICTED_INPUTS_NAME,
    PREDICTED_LABELS_NAME,
    PREDICTED_OUTPUTS_NAME,
    load_jsonl,
    outputs_to_labels,
)
from qaai.eval.spec import EvalSpec, load_spec

PathLike = Union[str, Path]

# Run-metadata fields surfaced in the viewer header (a readable subset of run_metadata.json).
_META_KEYS = ("spec", "component", "model", "prompt_set", "git_sha", "mlflow_run_id")


def _entity_id(row: Any) -> Optional[str]:
    """Best-effort human id for a record (mirrors harness._entity_id)."""
    if not isinstance(row, dict):
        return None
    req = row.get("requirement")
    if isinstance(req, dict) and req.get("req_id"):
        return req["req_id"]
    tc = row.get("test_case")
    if isinstance(tc, dict) and tc.get("test_id"):
        return tc["test_id"]
    if row.get("hazard_id"):
        return row["hazard_id"]
    haz = row.get("hazard")
    if isinstance(haz, dict) and haz.get("hazard_id"):
        return haz["hazard_id"]
    return None


def _read_metadata(pdir: Path) -> Dict[str, Any]:
    meta_path = pdir / METADATA_NAME
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _find_up(start: Path, rel: str) -> Optional[Path]:
    """Search ``start`` then each ancestor for ``rel``; return the first that exists."""
    for base in [start, *start.parents]:
        cand = base / rel
        if cand.exists():
            return cand
    return None


def _resolve_spec_path(meta: Dict[str, Any], pdir: Path) -> Path:
    name = meta.get("spec")
    if not name:
        raise FileNotFoundError(
            f"cannot determine the eval spec: {METADATA_NAME} in {pdir} has no 'spec' field. "
            f"Pass --spec eval/specs/<name>.yaml explicitly."
        )
    rel = f"eval/specs/{name}.yaml"
    found = _find_up(pdir, rel) or (Path.cwd() / rel if (Path.cwd() / rel).exists() else None)
    if not found:
        raise FileNotFoundError(
            f"spec '{name}' from {METADATA_NAME} not found as {rel} above {pdir} or in {Path.cwd()}. "
            f"Pass --spec explicitly."
        )
    return found


def _resolve_dataset_dir(meta: Dict[str, Any], pdir: Path) -> Path:
    """Locate the parent answer-key dataset dir (holds actual_outputs/actual_labels)."""
    for key in ("source_outputs_path", "source_inputs_path"):
        p = meta.get(key)
        if p and Path(p).exists():
            return Path(p).parent
    # Fallback: predictions/<ts>/ -> <dataset_dir>/predictions/<ts>, so up two levels.
    return pdir.parent.parent


def _load_outputs_lines(path: Path) -> List[Optional[Any]]:
    """Load a *_outputs.jsonl where soft-failed rows are the literal ``null``."""
    if not path.exists():
        return []
    rows: List[Optional[Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _flat(spec: EvalSpec, label_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A flat labels row -> {'verdict': ..., 'rubric': {code: verdict}} via the spec."""
    if not label_row:
        return {"verdict": None, "rubric": {}}
    verdict, rubric = spec.extract_label(label_row)
    return {"verdict": verdict, "rubric": rubric}


def load_comparison(
    predictions_dir: PathLike,
    *,
    spec_path: Optional[PathLike] = None,
    dataset_dir: Optional[PathLike] = None,
) -> List[Dict[str, Any]]:
    """Build the merged actual-vs-predicted comparison records for one run.

    Auto-resolves the spec and the parent dataset from ``run_metadata.json`` when not
    passed. Rows are positionally aligned (the dataset's core invariant); the actual side
    is truncated to the number of predicted rows (a ``--limit`` run produces fewer).
    """
    pdir = Path(predictions_dir)
    if not pdir.is_dir():
        raise FileNotFoundError(f"predictions dir not found: {pdir}")

    meta = _read_metadata(pdir)
    spec = load_spec(spec_path or _resolve_spec_path(meta, pdir))
    ds_dir = Path(dataset_dir) if dataset_dir else _resolve_dataset_dir(meta, pdir)

    # ── predicted side (this run) ──
    pred_inputs = load_jsonl(pdir / PREDICTED_INPUTS_NAME) if (pdir / PREDICTED_INPUTS_NAME).exists() else []
    pred_outputs = _load_outputs_lines(pdir / PREDICTED_OUTPUTS_NAME)
    if not pred_outputs:
        raise FileNotFoundError(f"no {PREDICTED_OUTPUTS_NAME} in {pdir} — is this a --mode run predictions folder?")
    pred_labels_path = pdir / PREDICTED_LABELS_NAME
    pred_labels = load_jsonl(pred_labels_path) if pred_labels_path.exists() else outputs_to_labels(spec, pred_outputs)

    n = len(pred_outputs)

    # ── actual side (parent answer key), truncated to the run's length ──
    actual_outputs = _load_outputs_lines(ds_dir / ACTUAL_OUTPUTS_NAME)
    actual_labels_path = ds_dir / ACTUAL_LABELS_NAME
    actual_labels = load_jsonl(actual_labels_path) if actual_labels_path.exists() else outputs_to_labels(spec, actual_outputs)
    if not actual_labels:
        raise FileNotFoundError(
            f"no answer key found beside the predictions: expected {ACTUAL_LABELS_NAME} or "
            f"{ACTUAL_OUTPUTS_NAME} in {ds_dir}. Pass --dataset-dir to point at the parent dataset."
        )

    if len(actual_labels) < n or (actual_outputs and len(actual_outputs) < n):
        raise ValueError(
            f"answer key in {ds_dir} has fewer rows ({len(actual_labels)}) than predictions ({n}); "
            f"cannot align. Point --dataset-dir at the dataset these predictions were produced from."
        )
    if len(pred_labels) != n or (pred_inputs and len(pred_inputs) != n):
        raise ValueError(f"prediction files in {pdir} disagree on length (outputs={n}); folder is corrupt.")

    codes = spec.output.rubric.codes if spec.output.rubric else spec.labels.rubric_keys
    advisory = set(spec.scoring.advisory_codes)
    verdict_key = spec.labels.verdict_key
    run_meta = {k: meta.get(k) for k in _META_KEYS if meta.get(k) is not None}

    records: List[Dict[str, Any]] = []
    for i in range(n):
        actual = _flat(spec, actual_labels[i])
        predicted = _flat(spec, pred_labels[i])

        diff: List[Dict[str, Any]] = []
        if actual["verdict"] != predicted["verdict"]:
            diff.append({"cell": verdict_key, "actual": actual["verdict"],
                         "predicted": predicted["verdict"], "mandatory": True})
        for code in codes:
            a = actual["rubric"].get(code)
            p = predicted["rubric"].get(code)
            if a != p:
                diff.append({"cell": code, "actual": a, "predicted": p,
                             "mandatory": code not in advisory})

        records.append({
            "entity_id": _entity_id(pred_inputs[i]) if pred_inputs else (_entity_id(actual_outputs[i]) if i < len(actual_outputs) else None),
            "inputs": pred_inputs[i] if pred_inputs else {},
            "verdict_key": verdict_key,
            "codes": list(codes),
            "run_meta": run_meta,
            "actual": actual,
            "predicted": predicted,
            "actual_output": actual_outputs[i] if i < len(actual_outputs) else None,
            "predicted_output": pred_outputs[i],
            "diff": diff,
            "verdict_match": actual["verdict"] == predicted["verdict"],
            "predicted_skipped": predicted["verdict"] is None,
        })
    return records


def write_compare(
    predictions_dir: PathLike,
    output: Optional[PathLike] = None,
    *,
    spec_path: Optional[PathLike] = None,
    dataset_dir: Optional[PathLike] = None,
) -> Path:
    """Build the comparison and write ``compare.html`` into the predictions folder.

    Returns the output path.
    """
    from qaai.viewer.generator import build_viewer_compare

    pdir = Path(predictions_dir)
    records = load_comparison(pdir, spec_path=spec_path, dataset_dir=dataset_dir)
    out = Path(output) if output else pdir / "compare.html"
    run_key = pdir.name or "compare"
    html = build_viewer_compare(records, source_label=str(pdir), run_key=run_key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("predictions_dir", help="Path to a predictions/<ts>/ folder")
    ap.add_argument("-o", "--output", default=None,
                    help="Output HTML path (default: compare.html inside the predictions folder)")
    ap.add_argument("--spec", default=None,
                    help="Override the eval spec (default: resolved from run_metadata.json's 'spec')")
    ap.add_argument("--dataset-dir", default=None,
                    help="Override the parent answer-key dataset dir (default: resolved from run_metadata.json)")
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="Open the written HTML in the default browser")
    args = ap.parse_args(argv)

    from qaai.viewer.generator import build_viewer_compare

    pdir = Path(args.predictions_dir)
    try:
        records = load_comparison(pdir, spec_path=args.spec, dataset_dir=args.dataset_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out = Path(args.output) if args.output else pdir / "compare.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_viewer_compare(records, source_label=str(pdir), run_key=pdir.name or "compare"),
        encoding="utf-8",
    )
    mism = sum(1 for r in records if not r["verdict_match"])
    print(f"wrote {out}  ({len(records)} records, {mism} verdict mismatches)")
    if args.open_browser:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
