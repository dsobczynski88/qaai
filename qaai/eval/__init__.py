"""Spec-driven MLflow evaluation harness for the QAAI reviewer pipelines.

Treats each LangGraph reviewer as a binary classifier (``overall_verdict`` Yes/No)
plus a per-cell rubric classifier, scores predictions against a labelled dataset,
and logs the run to MLflow. The *shape* of every reviewer's input / output / label
schema is described by a YAML ``EvalSpec`` (see ``eval/specs/``) so the same harness
works across reviewers and projects without code changes.

Public surface:
    load_spec(path) -> EvalSpec
    build_records(spec, outputs, labels, ...) -> list[RecordResult]
    compute_metrics(spec, records) -> dict
    sample_size / achieved-margin helpers (qaai.eval.sample_size)
"""

from qaai.eval.spec import EvalSpec, load_spec, get_path
from qaai.eval.scoring import RecordResult, build_records, compute_metrics

__all__ = [
    "EvalSpec",
    "load_spec",
    "get_path",
    "RecordResult",
    "build_records",
    "compute_metrics",
]
