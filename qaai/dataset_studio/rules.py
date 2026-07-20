"""Per-type verdict-derivation and N-A rules.

The three reviewers aggregate their rubric into an overall verdict by three
genuinely different rules, and the live models enforce them to three different
degrees:

``test_suite``
    :meth:`SynthesizedAssessment._derive_overall_verdict` recomputes and **silently
    corrects** ``overall_verdict`` on every validation, excluding ``ADVISORY_CODES``
    (``R6``). Yes iff every mandatory cell is in ``{Yes, N-A}``.

``hazard``
    :class:`HazardAssessment` has **no** derivation validator — aggregation lives in
    ``_FinalAssessorNode._aggregate_verdict``. Same pass rule, R7 excluded.

``test_case``
    ⚠ :meth:`TestCaseAssessment._validate_overall_verdict`
    (``qaai/agents/test_case_reviewer/core.py:192-202``) computes ``expected_verdict``
    and then ``pass``es — it catches nothing. The studio's ``V040`` check is the only
    thing guarding TC verdict drift. There is also no N-A branch:
    ``EvaluatedReviewObjective.verdict`` is ``Verdict``, not ``VerdictNA``.
    ``test_case_setup_clarity`` is advisory, like R6/R7 — every shipping
    ``single_test_aggregator`` prompt fixes its ``mandatory`` flag to false, and v8+
    embed the objective list so it is not configurable at run time. A row may still
    disagree via its own ``mandatory`` field, which is what ``mandatory_flags`` is for.

:func:`derive_overall_verdict` implements all three from the spec's
``mandatory_codes`` (so the advisory-code exclusion can never drift from what the
scorer does) plus the small per-type table below. Agreement with the live RTM
validator is pinned by ``tests/unit/dataset_studio/test_rules_derivation.py`` rather
than by parsing through the model at validation time — the oracle answer-key shape
omits fields the model requires, so it cannot always be parsed.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from qaai.eval.spec import EvalSpec

__all__ = [
    "NA_ALLOWED",
    "NA_COUNTS_AS_PASS",
    "na_allowed_for",
    "derive_overall_verdict",
]

# Rubric codes whose verdict may be N-A, per the field docs on each finding model.
#   test_suite — MandatoryFinding.verdict: "Only M2, M3, and R6 may be N-A."
#   hazard     — HazardFinding.verdict: "Only H5 may be N-A." R7 is Yes/No but is
#                advisory, and treating a missing advisory as N-A is harmless since
#                it never reaches the verdict computation.
#   test_case  — EvaluatedReviewObjective.verdict is Verdict (Yes/No). No N-A.
NA_ALLOWED: Dict[str, frozenset] = {
    "test_suite": frozenset({"M2", "M3", "R6"}),
    "hazard": frozenset({"H5", "R7"}),
    "test_case": frozenset(),
}

# Whether an N-A mandatory cell passes the gate. True for the two rubrics that admit
# N-A at all; irrelevant (and False) for test_case, which has no N-A.
NA_COUNTS_AS_PASS: Dict[str, bool] = {
    "test_suite": True,
    "hazard": True,
    "test_case": False,
}


def na_allowed_for(dataset_type: str) -> frozenset:
    """N-A-permitting rubric codes for a dataset type (empty set if unknown)."""
    return NA_ALLOWED.get(dataset_type, frozenset())


def derive_overall_verdict(
    dataset_type: str,
    spec: EvalSpec,
    rubric: Mapping[str, Any],
    *,
    mandatory_flags: Optional[Mapping[str, bool]] = None,
) -> Optional[str]:
    """Recompute the overall verdict from the rubric cells.

    ``rubric`` is ``{code: verdict}`` as produced by
    :meth:`EvalSpec.extract_prediction`, so this works identically on a minimal
    oracle row and a full graph-state row.

    ``mandatory_flags`` is the per-row ``{code: mandatory}`` map read from a
    full-shape test-case checklist. When supplied it overrides the spec's static
    ``mandatory_codes``, because ``EvaluatedReviewObjective.mandatory`` is a data
    field the row itself carries. The other two reviewers have no such field.

    Returns ``None`` when no mandatory cell is present — there is nothing to derive
    from, so the stated verdict stands (mirroring the live RTM validator's own
    ``if not mandatory: return self``).
    """
    codes = list(spec.mandatory_codes)
    if mandatory_flags is not None:
        codes = [c for c in spec.output.rubric.codes if mandatory_flags.get(c, True)] if spec.output.rubric else []

    present = [c for c in codes if c in rubric and rubric[c] is not None]
    if not present:
        return None

    passing = {spec.scoring.positive_label}
    if NA_COUNTS_AS_PASS.get(dataset_type, False):
        passing.add(spec.scoring.na_label)

    ok = all(rubric[c] in passing for c in present)
    return spec.scoring.positive_label if ok else spec.scoring.negative_label
