"""Score reviewer predictions against gold labels.

Two levels, matching the reviewer's own structure:
  * **overall verdict** — binary {positive, negative}: accuracy / precision / recall /
    F1 (positive class = ``spec.scoring.positive_label``) + macro-F1, balanced accuracy,
    Cohen's kappa, and class prevalence + a confusion matrix.
  * **per-rubric cells** — multiclass {Yes, No, N-A} by default: per-cell accuracy,
    macro-F1, balanced accuracy, kappa, and per-class support, plus an aggregate
    ``rubric_macro_f1``.

Balanced accuracy and macro-F1 are the ones to read when a class dominates; plain
accuracy flatters a model that always guesses the majority label. ``support_by_class``
is what tells you whether a given cell's number is trustworthy at all.

Three QAAI-specific signals are also computed:
  * **exact-match rate** — fraction of records where *every mandatory* rubric cell is
    right (advisory codes excluded). Strict row-level correctness.
  * **helper-invariant pass-rate** — does the model's overall verdict equal the
    deterministic rule "Yes iff every mandatory cell ∈ {positive, N-A}"? A drop here
    means the synthesizer/aggregator contradicted its own rubric.
  * **skip rate** — fraction of records whose prediction could not be extracted
    (soft-failed node / incomplete output).

All computation is pure and LLM-free, so it is unit-testable in isolation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    cohen_kappa_score,
    f1_score,
    precision_recall_fscore_support,
)

from qaai.eval.spec import EvalSpec


@dataclass
class RecordResult:
    """Per-record prediction vs. ground truth, plus provenance for artifacts."""
    idx: int
    entity_id: Optional[str]
    gt_overall: Optional[str]
    pred_overall: Optional[str]
    # one dict per rubric code: {"code", "gt", "pred", "match"}
    per_rubric: List[Dict[str, Any]] = field(default_factory=list)
    complete: bool = True
    latency_s: Optional[float] = None
    error: Optional[str] = None

    @property
    def scored(self) -> bool:
        """A record contributes to overall metrics only if both verdicts exist."""
        return self.complete and self.gt_overall is not None and self.pred_overall is not None

    @property
    def overall_match(self) -> Optional[bool]:
        if self.gt_overall is None or self.pred_overall is None:
            return None
        return self.pred_overall == self.gt_overall

    def to_json(self) -> Dict[str, Any]:
        return {
            "idx": self.idx,
            "entity_id": self.entity_id,
            "gt_overall": self.gt_overall,
            "pred_overall": self.pred_overall,
            "overall_match": self.overall_match,
            "per_rubric": self.per_rubric,
            "complete": self.complete,
            "latency_s": self.latency_s,
            "error": self.error,
        }


def build_records(
    spec: EvalSpec,
    outputs: Sequence[Any],
    labels: Sequence[Dict[str, Any]],
    *,
    entity_ids: Optional[Sequence[Optional[str]]] = None,
    latencies: Optional[Sequence[Optional[float]]] = None,
    completes: Optional[Sequence[bool]] = None,
    errors: Optional[Sequence[Optional[str]]] = None,
) -> List[RecordResult]:
    """Pair each output row with its label row (row-aligned) into RecordResults.

    ``outputs`` may be plain dicts (score-only) or graph-state dicts holding Pydantic
    models (run+score) — extraction handles both. ``completes`` lets the runner mark a
    record incomplete even when a partial verdict is present (e.g. failed is_complete).
    """
    n = min(len(outputs), len(labels))
    records: List[RecordResult] = []
    for i in range(n):
        out_row = outputs[i]
        label_row = labels[i]
        pred_overall, pred_rubric = (None, {}) if out_row is None else spec.extract_prediction(out_row)
        gt_overall, gt_rubric = spec.extract_label(label_row)

        per_rubric: List[Dict[str, Any]] = []
        codes = spec.output.rubric.codes if spec.output.rubric else []
        for code in codes:
            gt_v = gt_rubric.get(code)
            pred_v = pred_rubric.get(code)
            if gt_v is None and pred_v is None:
                continue
            per_rubric.append(
                {"code": code, "gt": gt_v, "pred": pred_v, "match": gt_v == pred_v}
            )

        complete = True if completes is None else bool(completes[i])
        records.append(
            RecordResult(
                idx=i,
                entity_id=(entity_ids[i] if entity_ids else None),
                gt_overall=gt_overall,
                pred_overall=pred_overall,
                per_rubric=per_rubric,
                complete=complete,
                latency_s=(latencies[i] if latencies else None),
                error=(errors[i] if errors else None),
            )
        )
    return records


def _binary(labels: List[str], positive: str) -> List[int]:
    return [1 if v == positive else 0 for v in labels]


def _safe_kappa(y_true: Sequence[Any], y_pred: Sequence[Any]) -> Optional[float]:
    """Cohen's kappa, or None when it is undefined.

    Kappa divides by (1 - expected agreement), which is 0 when both raters use a
    single class throughout (e.g. an M-cell whose gt and pred are all "Yes").
    sklearn returns nan there; MLflow would happily log the nan and it would read as
    a real score. None means "not computable for this sample" and the caller omits it.
    """
    import math

    if len(set(y_true)) < 2 and len(set(y_pred)) < 2:
        return None
    k = float(cohen_kappa_score(y_true, y_pred))
    return None if math.isnan(k) else k


def _derive_overall(spec: EvalSpec, rubric: Dict[str, Optional[str]]) -> Optional[str]:
    """Deterministic rule: positive iff every mandatory cell ∈ {positive, N-A}."""
    mandatory = spec.mandatory_codes
    ok = {spec.scoring.positive_label, spec.scoring.na_label}
    present = [rubric[c] for c in mandatory if rubric.get(c) is not None]
    if not present:
        return None
    return spec.scoring.positive_label if all(v in ok for v in present) else spec.scoring.negative_label


def compute_metrics(spec: EvalSpec, records: List[RecordResult]) -> Dict[str, Any]:
    """Reduce RecordResults to a nested metrics dict (see metrics.py for the flat MLflow view)."""
    enabled = set(spec.mlflow.metrics_enabled)
    total = len(records)
    scored = [r for r in records if r.scored]
    out: Dict[str, Any] = {
        "n_total": total,
        "n_scored": len(scored),
        "skip_rate": (total - len(scored)) / total if total else 0.0,
    }

    # --- Overall binary classifier ---
    if "overall" in enabled and scored:
        y_true = [r.gt_overall for r in scored]
        y_pred = [r.pred_overall for r in scored]
        pos = spec.scoring.positive_label
        yt, yp = _binary(y_true, pos), _binary(y_pred, pos)
        prec, rec, f1, _ = precision_recall_fscore_support(
            yt, yp, average="binary", pos_label=1, zero_division=0
        )
        n = len(yt)
        overall: Dict[str, Any] = {
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "precision": float(prec),
            "recall": float(rec),
            # Positive-class F1 (kept for continuity) alongside macro-F1, which
            # weights Yes and No equally and is the honest headline under imbalance.
            "f1": float(f1),
            "f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
            "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
            "support_positive": int(sum(yt)),
            "support_negative": int(n - sum(yt)),
            "prevalence_gt_positive": float(sum(yt) / n),
            "prevalence_pred_positive": float(sum(yp) / n),
        }
        kappa = _safe_kappa(y_true, y_pred)
        if kappa is not None:
            overall["cohen_kappa"] = kappa
        out["overall"] = overall

    # --- Per-rubric multi-cell classifier ---
    if "per_rubric" in enabled and spec.output.rubric:
        per_rubric: Dict[str, Any] = {}
        macro_f1s: List[float] = []
        collapse = spec.scoring.rubric_class_mode == "binary_collapse"
        for code in spec.output.rubric.codes:
            pairs = [
                (rc["gt"], rc["pred"])
                for r in records
                for rc in r.per_rubric
                if rc["code"] == code and rc["gt"] is not None and rc["pred"] is not None
            ]
            if not pairs:
                continue
            gt = [g for g, _ in pairs]
            pd = [p for _, p in pairs]
            if collapse:
                pos = spec.scoring.positive_label
                gt = [pos if v == spec.scoring.na_label else v for v in gt]
                pd = [pos if v == spec.scoring.na_label else v for v in pd]
            cell_f1 = float(f1_score(gt, pd, average="macro", zero_division=0))
            cell: Dict[str, Any] = {
                "accuracy": float(accuracy_score(gt, pd)),
                "f1_macro": cell_f1,
                "balanced_accuracy": float(balanced_accuracy_score(gt, pd)),
                "support": len(pairs),
                # Per-class ground-truth counts: a cell whose minority class has a
                # handful of rows has a wide CI no matter how good its accuracy looks.
                "support_by_class": dict(Counter(gt)),
            }
            cell_kappa = _safe_kappa(gt, pd)
            if cell_kappa is not None:
                cell["cohen_kappa"] = cell_kappa
            per_rubric[code] = cell
            if code not in spec.scoring.advisory_codes:
                macro_f1s.append(cell_f1)
        out["per_rubric"] = per_rubric
        if macro_f1s:
            out["rubric_macro_f1"] = float(sum(macro_f1s) / len(macro_f1s))

    # --- Exact match: every mandatory cell correct on a record ---
    if "exact_match" in enabled and spec.output.rubric:
        mandatory = set(spec.mandatory_codes)
        flags: List[bool] = []
        for r in scored:
            cells = [
                rc for rc in r.per_rubric
                if rc["code"] in mandatory and rc["gt"] is not None and rc["pred"] is not None
            ]
            # No comparable mandatory cells => nothing to be right about. Counting this
            # as a pass would inflate the rate with unlabelled rows.
            if not cells:
                continue
            flags.append(all(rc["match"] for rc in cells))
        if flags:
            out["exact_match_rate"] = sum(flags) / len(flags)
            out["exact_match_n"] = len(flags)

    # --- Helper-invariant pass rate (predicted overall vs deterministic derivation) ---
    if "helper_invariant" in enabled and spec.output.rubric:
        checks = []
        for r in scored:
            pred_rubric = {rc["code"]: rc["pred"] for rc in r.per_rubric}
            derived = _derive_overall(spec, pred_rubric)
            if derived is not None:
                checks.append(derived == r.pred_overall)
        if checks:
            out["helper_invariant_pass_rate"] = sum(checks) / len(checks)

    # --- Latency ---
    if "latency" in enabled:
        lats = [r.latency_s for r in records if r.latency_s is not None]
        if lats:
            import numpy as np

            out["latency"] = {
                "mean_s": float(np.mean(lats)),
                "p50_s": float(np.percentile(lats, 50)),
                "p95_s": float(np.percentile(lats, 95)),
                "p99_s": float(np.percentile(lats, 99)),
            }

    return out
