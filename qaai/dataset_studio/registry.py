"""Bind a dataset type to its eval spec, graph state, and per-row Pydantic models.

This is the single place that knows ``"test_suite"`` means ``RTMReviewState`` +
``eval/specs/test_suite_reviewer.yaml``. Everything downstream (validation, the
editor, the save server) asks this module rather than hard-coding a rubric.

Row models are built by **projection**, not by hand: :func:`input_row_model` reads
the graph state's own annotations via ``typing.get_type_hints`` and keeps only the
keys the spec's ``input:`` block names. Adding ``user_needs`` to a reviewer state
and to its spec makes the validator type-check that field with no edit here.

Two output-row shapes exist in the wild and both are legitimate answer keys:

``minimal``
    What :func:`qaai.eval.datasets.synthesize_outputs` emits — the verdict plus a
    findings list carrying only ``{code, verdict}``. This is the shape of the
    committed pilot's ``eval/datasets/test_suite/actual/<ts>/actual_outputs.jsonl``.
    It deliberately
    omits fields the live models require (``MandatoryFinding.dimension`` /
    ``.rationale``, ``SynthesizedAssessment.requirement``), so validating it against
    the full model would report a wall of spurious "missing field" errors.

``full``
    A real graph run's output state, which does satisfy the full model.

:func:`output_row_shape` classifies a row so the validator can apply full-model
validation only where it is meaningful, and fall back to structural + derivation
checks otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from qaai.agents.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardFinding,
    HazardReviewState,
)
from qaai.agents.test_case_reviewer.core import (
    EvaluatedReviewObjective,
    TCReviewState,
    TestCaseAssessment,
)
from qaai.agents.test_suite_reviewer.core import (
    MandatoryFinding,
    RTMReviewState,
    SynthesizedAssessment,
)
from qaai.eval.spec import EvalSpec, load_spec

__all__ = [
    "DATASET_TYPES",
    "DatasetTypeInfo",
    "SPECS_DIR",
    "dataset_type_for",
    "infer_dataset_type",
    "input_row_model",
    "load_type_spec",
    "output_row_model",
    "output_row_shape",
    "assessment_key",
]

SPECS_DIR = Path("eval/specs")

# Traceability context that every reviewer accepts but few dataset rows carry. The
# reviewer states disagree on whether it is Optional (RTMReviewState says yes,
# TCReviewState says no), and rows routinely omit it either way, so the projection
# treats it as optional uniformly rather than inheriting that inconsistency.
ALWAYS_OPTIONAL_INPUT_KEYS = frozenset({"design_docs"})


@dataclass(frozen=True)
class DatasetTypeInfo:
    """Everything type-specific about one dataset type, in one place."""

    name: str                       # "test_suite" | "test_case" | "hazard"
    component: str                  # also the eval/specs/<component>.yaml stem
    state_cls: type                 # the reviewer's graph-state TypedDict
    assessment_cls: type[BaseModel]  # the final assessment model
    finding_cls: type[BaseModel]     # one rubric cell
    label: str                      # human-readable, for CLI messages


DATASET_TYPES: Dict[str, DatasetTypeInfo] = {
    "test_suite": DatasetTypeInfo(
        name="test_suite",
        component="test_suite_reviewer",
        state_cls=RTMReviewState,
        assessment_cls=SynthesizedAssessment,
        finding_cls=MandatoryFinding,
        label="Test Suite Reviewer (RTM)",
    ),
    "test_case": DatasetTypeInfo(
        name="test_case",
        component="test_case_reviewer",
        state_cls=TCReviewState,
        assessment_cls=TestCaseAssessment,
        finding_cls=EvaluatedReviewObjective,
        label="Single Test Case Reviewer",
    ),
    "hazard": DatasetTypeInfo(
        name="hazard",
        component="hazard_risk_reviewer",
        state_cls=HazardReviewState,
        assessment_cls=HazardAssessment,
        finding_cls=HazardFinding,
        label="Hazard Coverage Reviewer",
    ),
}


def dataset_type_for(name: str) -> DatasetTypeInfo:
    """Look up a dataset type, raising a CLI-friendly error for an unknown name."""
    try:
        return DATASET_TYPES[name]
    except KeyError:
        raise KeyError(
            f"unknown dataset type {name!r}; expected one of {', '.join(DATASET_TYPES)}"
        ) from None


def infer_dataset_type(dataset_dir: Union[str, Path]) -> Optional[str]:
    """Infer the type from a dataset path, or return None.

    Walks the path and its ancestors, nearest first, so every layout in use resolves:

    * ``eval/datasets/<type>/actual/<ts>/`` — what :func:`scaffold.new_dataset_dir`
      produces today (the type is two levels up)
    * ``eval/datasets/<type>/<ts>/`` — datasets scaffolded before the ``actual/``
      segment was introduced
    * ``eval/datasets/<type>/`` — the legacy flat layout of the committed pilot

    Nearest-first matters: it keeps the innermost type directory authoritative if a
    dataset is ever nested under another type's tree.
    """
    p = Path(dataset_dir)
    for candidate in (p.name, *(a.name for a in p.parents)):
        if candidate in DATASET_TYPES:
            return candidate
    return None


def load_type_spec(
    info: DatasetTypeInfo, override: Optional[Union[str, Path]] = None
) -> EvalSpec:
    """Load the eval spec for a dataset type (``eval/specs/<component>.yaml``)."""
    path = Path(override) if override else SPECS_DIR / f"{info.component}.yaml"
    return load_spec(path)


def assessment_key(spec: EvalSpec) -> str:
    """The top-level state key holding the assessment, e.g. ``synthesized_assessment``."""
    return spec.output.verdict_path.split(".")[0]


def _is_optional(annotation: Any) -> bool:
    return type(None) in get_args(annotation) and get_origin(annotation) is not None


def _state_hints(state_cls: type) -> Dict[str, Any]:
    """Resolved annotations for a reviewer state TypedDict.

    ``include_extras=False`` strips the ``Annotated[..., operator.add]`` reducer
    wrappers, so a fan-in field resolves to its plain ``List[X]`` element type.
    """
    return get_type_hints(state_cls, include_extras=False)


def input_row_model(info: DatasetTypeInfo, spec: EvalSpec) -> type[BaseModel]:
    """Build the Pydantic model for one ``actual_inputs.jsonl`` row.

    Fields are the row paths named by ``spec.input``, typed by the corresponding
    graph-state annotation. Extra keys are allowed: a dataset row may legitimately
    carry authoring metadata (``rationale``, ``expected_gap``, ``id``) that is not
    part of the graph input.
    """
    hints = _state_hints(info.state_cls)
    fields: Dict[str, Any] = {}
    for state_key, row_path in spec.input.items():
        # Only flat row paths can become model fields. A dotted path would mean the
        # value is nested inside the row; no shipped spec does this, so it degrades
        # to an untyped optional rather than failing the whole model build.
        field_name = row_path.split(".")[0]
        annotation = hints.get(state_key, Any)
        dotted = "." in row_path
        optional = (
            dotted
            or state_key in ALWAYS_OPTIONAL_INPUT_KEYS
            or _is_optional(annotation)
        )
        if dotted:
            annotation = Any
        fields[field_name] = (
            (Optional[annotation], None) if optional else (annotation, ...)
        )
    return create_model(
        f"{info.name.title().replace('_', '')}InputRow",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


def output_row_model(info: DatasetTypeInfo, spec: EvalSpec) -> type[BaseModel]:
    """Build the Pydantic model for one **full-shape** ``actual_outputs.jsonl`` row.

    Only the assessment key is typed; the rest of a run's state is allowed through
    as extra. Do not apply this to a minimal/oracle row — see
    :func:`output_row_shape`.
    """
    key = assessment_key(spec)
    return create_model(
        f"{info.name.title().replace('_', '')}OutputRow",
        __config__=ConfigDict(extra="allow"),
        **{key: (info.assessment_cls, ...)},
    )


def _required_fields(model: type[BaseModel]) -> set:
    return {n for n, f in model.model_fields.items() if f.is_required()}


def output_row_shape(spec: EvalSpec, row: Any, info: DatasetTypeInfo) -> str:
    """Classify an output row as ``"minimal"``, ``"full"``, or ``"empty"``.

    The question this answers is precisely "can the live model validate this row?",
    so it is decided by whether the row supplies fields the model *requires* — not by
    the presence of any extra key. A cell carrying ``{code, verdict, partial}`` is
    still oracle-shaped (``partial`` has a default); a cell carrying ``rationale``
    is claiming to be a real assessment and gets held to the full model.

    ``minimal`` is the oracle projection produced by
    :func:`qaai.eval.datasets.synthesize_outputs` — the shape of the committed
    ``eval/datasets/test_suite/actual/<ts>/actual_outputs.jsonl``. A row with no assessment at
    all is ``empty``.
    """
    if not isinstance(row, dict):
        return "empty"
    assessment = row.get(assessment_key(spec))
    if not isinstance(assessment, dict):
        return "empty"

    rub = spec.output.rubric
    # Fields the minimal projection always writes; their presence proves nothing.
    projected_top = {spec.output.verdict_path.split(".")[-1]}
    if rub:
        projected_top.add(rub.list_path.split(".")[-1])
    if (_required_fields(info.assessment_cls) - projected_top) & set(assessment):
        return "full"

    if rub:
        projected_cell = {rub.code_field, rub.verdict_field}
        demanded = _required_fields(info.finding_cls) - projected_cell
        for cell in assessment.get(rub.list_path.split(".")[-1]) or []:
            if isinstance(cell, dict) and demanded & set(cell):
                return "full"
    return "minimal"
