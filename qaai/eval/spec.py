"""Eval spec model — the "different eval models" abstraction.

An ``EvalSpec`` declares, for one reviewer/project, exactly where the prediction
lives in a graph-output row and how to read the gold labels, so the scorer never
hard-codes a schema. Swapping reviewers (RTM / hazard / test-case) or projects is a
matter of writing a new ``eval/specs/<name>.yaml`` — no Python change.

The extraction helpers deliberately read from *both* plain dicts (score-only mode,
where ``eval_outputs.jsonl`` rows are JSON) and Pydantic models (run+score mode,
where ``graph.ainvoke`` returns a state dict holding model instances). This mirrors
``qaai/api/services.py::_field``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field


def _get(obj: Any, key: str) -> Any:
    """Read one attribute/key from a dict or a Pydantic model (final states hold both)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    if hasattr(obj, key):
        return getattr(obj, key)
    if hasattr(obj, "model_dump"):
        return obj.model_dump().get(key)
    return None


def get_path(obj: Any, dotted: str) -> Any:
    """Resolve a dotted path (e.g. ``synthesized_assessment.overall_verdict``).

    Returns ``None`` if any segment is missing — never raises — so a soft-failed
    node (missing assessment) scores as an unextractable prediction rather than a
    crash.
    """
    cur = obj
    for part in dotted.split("."):
        cur = _get(cur, part)
        if cur is None:
            return None
    return cur


class RubricSpec(BaseModel):
    """Where the per-cell rubric findings live and how each cell is keyed."""
    list_path: str
    code_field: str = "code"
    verdict_field: str = "verdict"
    codes: List[str] = Field(default_factory=list)


class OutputSpec(BaseModel):
    """Where predictions live in an output/state row."""
    verdict_path: str
    rubric: Optional[RubricSpec] = None


class LabelSpec(BaseModel):
    """How to read gold labels from an ``eval_outputs_labels`` row (flat dict)."""
    verdict_key: str = "Overall_Verdict"
    rubric_keys: List[str] = Field(default_factory=list)


class ScoringSpec(BaseModel):
    """Verdict vocabulary + which rubric cells are advisory (excluded from the headline)."""
    positive_label: str = "Yes"
    negative_label: str = "No"
    na_label: str = "N-A"
    advisory_codes: List[str] = Field(default_factory=list)
    # "multiclass" scores rubric cells over {Yes, No, N-A}; "binary_collapse"
    # folds N-A into the positive class before scoring.
    rubric_class_mode: str = "multiclass"


class MlflowSpec(BaseModel):
    """Experiment naming + declarative param/tag/metric knobs (add/remove here)."""
    experiment: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tags: Dict[str, Any] = Field(default_factory=dict)
    metrics_enabled: List[str] = Field(
        default_factory=lambda: ["overall", "per_rubric", "latency", "cost", "helper_invariant"]
    )


class EvalSpec(BaseModel):
    """Top-level eval spec loaded from ``eval/specs/<name>.yaml``."""
    name: str
    component: str
    prompt_set: Optional[str] = None
    # run+score input builders: logical graph-state key -> dotted path in the eval_inputs row
    input: Dict[str, str] = Field(default_factory=dict)
    output: OutputSpec
    labels: LabelSpec = Field(default_factory=LabelSpec)
    scoring: ScoringSpec = Field(default_factory=ScoringSpec)
    mlflow: MlflowSpec = Field(default_factory=MlflowSpec)

    @property
    def mandatory_codes(self) -> List[str]:
        """Rubric codes that count toward the overall verdict (advisory excluded)."""
        if not self.output.rubric:
            return []
        adv = set(self.scoring.advisory_codes)
        return [c for c in self.output.rubric.codes if c not in adv]

    def extract_prediction(self, out_row: Any) -> tuple[Optional[str], Dict[str, Any]]:
        """Pull (overall_verdict, {code: verdict}) from an output/state row."""
        verdict = get_path(out_row, self.output.verdict_path)
        rubric: Dict[str, Any] = {}
        if self.output.rubric:
            items = get_path(out_row, self.output.rubric.list_path) or []
            for item in items:
                code = _get(item, self.output.rubric.code_field)
                if code is not None:
                    rubric[str(code)] = _get(item, self.output.rubric.verdict_field)
        return verdict, rubric

    def extract_label(self, label_row: Dict[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
        """Pull (overall_verdict, {code: verdict}) from a flat labels row."""
        verdict = label_row.get(self.labels.verdict_key)
        keys = self.labels.rubric_keys or (self.output.rubric.codes if self.output.rubric else [])
        rubric = {k: label_row[k] for k in keys if k in label_row}
        return verdict, rubric


def load_spec(path: Union[str, Path]) -> EvalSpec:
    """Load and validate an eval spec from a YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvalSpec(**data)
