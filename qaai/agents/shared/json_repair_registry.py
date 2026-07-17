"""Centralized, documented repairs for LLM schema-fidelity malformations.

This module is the SINGLE documented surface for deterministic, non-fabricating
repairs applied to LLM JSON before Pydantic validation. Each function:

  * operates on plain ``dict`` / JSON-decoded data (NO Pydantic model imports, so
    this module has no dependency on any reviewer package and cannot create an
    import cycle),
  * is pure and idempotent — running it twice yields the same result,
  * NEVER fabricates data. It only re-homes a mislabeled value that already
    exists, unwraps an over-nested structure, or drops an item that cannot be
    deterministically coerced. When the malformation is not present, the input is
    returned unchanged so validation fails exactly as it would have.

Repairs are dispatched by the target model's class NAME (``response_model.__name__``)
via :data:`REPAIRS_BY_MODEL`, applied in order in
``BaseLLMNode._parse_llm_response`` (qaai/agents/shared/nodes.py) right after JSON
decoding and before ``response_model.model_validate``.

The reviewer models' own ``model_validator(mode="before")`` hooks delegate to these
same functions, so they remain a safety net for direct ``model_validate`` calls
(e.g. in tests / cache restore) while the logic lives in exactly one place.

To add support for a NEW malformation:
  1. capture its raw dump (``logs/**/failed_parse_*.txt`` — written automatically),
  2. drop it into ``tests/fixtures/malformed/<model>/`` (see
     ``scripts/harvest_failed_parses.py``),
  3. write/extend a repair function here and register it in ``REPAIRS_BY_MODEL``,
  4. the parametrized replay test (``tests/unit/shared/test_malformed_replay.py``)
     pins the recovery permanently.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (single source of truth for the alias/key sets)
# ---------------------------------------------------------------------------

# Keys the LLM has been observed to (or plausibly may) nest the decomposed-spec
# list under instead of the canonical ``decomposed_specifications``.
SPEC_KEY_ALIASES: Tuple[str, ...] = (
    "decomposed_requirements",
    "decomposed_specs",
    "specifications",
    "specs",
)

# Fields that identify a dict as a bare DecomposedSpec (the "flatten" malformation,
# where a spec dict appears where a DecomposedRequirement was expected).
_SPEC_MARKER_FIELDS = ("spec_id", "acceptance_criteria")

# Fields that identify a dict as a bare SummarizedTestCase (the "unwrapped singleton"
# malformation, where one summary object appears where a list of them was expected).
_SUMMARIZED_TC_MARKER_FIELDS = ("test_case_id", "objective")

# Keys the LLM has been observed to nest the summarized-test-case list under instead of
# returning the bare array that SummarizedTestCaseList requires.
SUMMARY_LIST_KEY_ALIASES: Tuple[str, ...] = (
    "summaries",
    "response",
    "summarized_test_cases",
    "test_cases",
)

# Verdict strings the LLM emits instead of (verdict='Yes', partial=True).
PARTIAL_VERDICT_ALIASES = {"partial", "yes-partial", "yes (partial)", "yes-with-partial"}

# Assessment-level keys that belong at the TOP level of TestCaseAssessment. When the
# aggregator wraps its whole answer under a single ``test_case`` key, these are lifted
# back out of ``test_case`` before validation.
ASSESSMENT_LEVEL_KEYS: Tuple[str, ...] = (
    "requirements",
    "decomposed_requirements",
    "evaluated_checklist",
    "overall_verdict",
    "comments",
    "clarification_questions",
)


# ---------------------------------------------------------------------------
# Primitive repairs
# ---------------------------------------------------------------------------


def coerce_partial_verdict(verdict: Any) -> Tuple[Any, bool]:
    """Return ``(canonical_verdict, partial_flag)`` when ``verdict`` matches a
    'Partial' alias the LLM emits instead of ``(verdict='Yes', partial=True)``.
    Returns the verdict unchanged with ``partial_flag=False`` when no coercion
    applies."""
    if isinstance(verdict, str) and verdict.strip().lower() in PARTIAL_VERDICT_ALIASES:
        return "Yes", True
    return verdict, False


def rehome_decomposed_specs(data: Any) -> Any:
    """Normalize ONE DecomposedRequirement dict whose spec list is under a wrong key.

    The decomposer / aggregator LLM intermittently nests the specs under
    ``decomposed_requirements`` (the parent list's own name) or another alias,
    which reads as the required ``decomposed_specifications`` field being absent.
    Re-homes the mislabeled list to ``decomposed_specifications`` only when the
    canonical key is absent — never clobbers a real value, never fabricates.
    """
    if not isinstance(data, dict) or "decomposed_specifications" in data:
        return data
    for alias in SPEC_KEY_ALIASES:
        if isinstance(data.get(alias), list):
            data = dict(data)
            data["decomposed_specifications"] = data.pop(alias)
            break
    return data


def unwrap_summarized_test_case_list(data: Any) -> Any:
    """Lift the summary list out of a wrapper object the LLM put it in.

    Observed variants (gpt-5.4-mini, SummaryNode)::

        {"req_id": "REQ-HC-100", "summaries": [ {...}, {...} ]}
        {"response": [ {...} ]}

    ``SummarizedTestCaseList`` is a ``RootModel[List[...]]`` and wants the bare array, so
    the wrapper reads as "not a list" and the whole batch is skipped. Returns the nested
    list when exactly that shape is present; the ``req_id`` echo is discarded because the
    model has no field for it and the caller already knows the requirement.

    ROOT CAUSE (fixed at source): these wrappers were the model obeying
    ``response_format={"type": "json_object"}`` — which forbids a top-level array — while
    still trying to honour the prompt's array schema. ``BaseLLMNode._wants_array_response``
    now suppresses JSON mode for array-root models, so a compliant endpoint returns the
    bare array and this repair is a no-op. Retained as a net for endpoints that force JSON
    mode regardless, and to keep the harvested corpus replayable.

    A dict that is itself a bare summary is left alone for
    :func:`wrap_bare_summarized_test_case` to handle. Never fabricates: if no alias holds
    a list, the input is returned unchanged and validation fails as it would have.
    """
    if not isinstance(data, dict):
        return data
    if all(k in data for k in _SUMMARIZED_TC_MARKER_FIELDS):
        return data  # a bare summary, not a wrapper
    for alias in SUMMARY_LIST_KEY_ALIASES:
        if isinstance(data.get(alias), list):
            logger.warning(
                "unwrap_summarized_test_case_list: lifted the summary list out of a "
                "%r wrapper key", alias,
            )
            return data[alias]
    return data


def wrap_bare_summarized_test_case(data: Any) -> Any:
    """Wrap ONE bare SummarizedTestCase object into the single-element list it should be.

    Observed malformation (gpt-5.4-mini, test_suite_reviewer SummaryNode)::

        {"test_case_id": "TC-HC-138-A", "objective": "...", "protocol": [...]}

    where ``SummarizedTestCaseList`` (a ``RootModel[List[SummarizedTestCase]]``) requires
    ``[{...}]``. Pydantic reports "Input should be a valid list" and the batch is skipped.

    ROOT CAUSE (fixed at source): ``response_format={"type": "json_object"}`` forbids a
    top-level array, so the model emitted the single *item* object — the closest legal
    object to the requested schema — regardless of how many test cases were in the batch.
    ``BaseLLMNode._wants_array_response`` now suppresses JSON mode for array-root models.
    Retained as a net for endpoints that force JSON mode regardless.

    Note this repair CANNOT distinguish "one test case, correctly summarised" from "four
    test cases, three dropped" — both arrive as one object. ``REQUIRE_COMPLETE_BATCH``
    (BatchedLLMNode) is what catches the latter, by comparing the recovered count against
    the batch size and skipping rather than judging on partial evidence.

    ``test_case_id`` + ``objective`` together identify the shape: both are required fields
    of SummarizedTestCase and neither appears on any other model routed through this
    parser. Wrapping re-homes a value that already exists in full and fabricates nothing;
    when the payload is already a list (or any other shape) this is a no-op, so validation
    still fails exactly as it would have.
    """
    if isinstance(data, dict) and all(k in data for k in _SUMMARIZED_TC_MARKER_FIELDS):
        logger.warning(
            "wrap_bare_summarized_test_case: wrapped a bare SummarizedTestCase (id=%s) "
            "into a single-element list",
            data.get("test_case_id"),
        )
        return [data]
    return data


def unwrap_wrapped_assessment(data: Any) -> Any:
    """Recover a TestCaseAssessment when the LLM nested every field inside ``test_case``.

    Observed malformation: the aggregator returns
    ``{"test_case": {<TestCase fields> + requirements + decomposed_requirements +
    evaluated_checklist + overall_verdict + comments + clarification_questions}}`` — the
    entire assessment wrapped one level too deep, so the parsed dict's only top-level key
    is ``test_case`` and the required top-level fields read as missing.

    ``evaluated_checklist`` is the sentinel: never a legitimate TestCase field, so its
    presence *inside* ``test_case`` while *absent* at the top level unambiguously signals
    the wrap. When detected, the assessment-level keys are lifted back to the top level
    (never clobbering a key already present there) and ``test_case`` is reduced to just its
    TestCase fields. Returns ``data`` unchanged when the wrap is not present; never
    fabricates.
    """
    if not isinstance(data, dict):
        return data
    tc = data.get("test_case")
    if not (
        isinstance(tc, dict)
        and "evaluated_checklist" not in data
        and "evaluated_checklist" in tc
    ):
        return data
    lifted = dict(data)
    inner = dict(tc)
    for key in ASSESSMENT_LEVEL_KEYS:
        if key in inner and key not in lifted:
            lifted[key] = inner.pop(key)
    lifted["test_case"] = inner
    return lifted


def normalize_assessment_decomposed_requirements(data: Any) -> Any:
    """Make the non-critical ``decomposed_requirements`` echo list resilient.

    ``TestCaseAssessment.decomposed_requirements`` is echo/reasoning material with a
    ``default_factory=list`` and does NOT gate ``overall_verdict``. A single malformed
    item must therefore never fail the whole assessment (which carries the verdict).

    For each item this:
      * re-homes a mislabeled spec list (:func:`rehome_decomposed_specs`), then
      * DROPS items that still cannot be validated as a DecomposedRequirement — the
        "flatten" variant, where the item is itself a bare spec dict (has ``spec_id`` but
        no wrapping ``requirement``). Dropping (rather than fabricating a ``requirement``)
        preserves the non-fabrication guarantee; the verdict and checklist are unaffected.

    Never fabricates; when every item is well-formed this is a no-op.
    """
    if not isinstance(data, dict) or not isinstance(data.get("decomposed_requirements"), list):
        return data

    kept: List[Any] = []
    dropped = 0
    for item in data["decomposed_requirements"]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        item = rehome_decomposed_specs(item)
        has_specs = isinstance(item.get("decomposed_specifications"), list)
        has_requirement = isinstance(item.get("requirement"), dict)
        looks_like_bare_spec = any(k in item for k in _SPEC_MARKER_FIELDS) and not has_requirement
        if has_requirement and has_specs:
            kept.append(item)
        elif looks_like_bare_spec or not has_requirement:
            # Flatten variant / missing requirement — uncoercible without fabricating.
            dropped += 1
        else:
            # requirement present but specs missing/malformed: keep and let Pydantic
            # decide (its own validator / defaults may still handle it).
            kept.append(item)

    if dropped:
        logger.warning(
            "normalize_assessment_decomposed_requirements: dropped %d uncoercible "
            "decomposed_requirements item(s) (non-critical echo field; verdict unaffected)",
            dropped,
        )
    data = dict(data)
    data["decomposed_requirements"] = kept
    return data


def repair_checklist_structure(data: Any, node_name: str = "") -> Any:
    """Add missing ``description`` / ``partial`` fields to ``evaluated_checklist`` items.

    The aggregator occasionally omits the (non-discriminating) ``description`` field; the
    model defaults it, but repairing here keeps the parse-path corpus self-documenting.
    Only fills absent keys — never overwrites a supplied value.
    """
    if not isinstance(data, dict):
        return data
    checklist = data.get("evaluated_checklist")
    if not isinstance(checklist, list):
        return data
    for i, item in enumerate(checklist):
        if not isinstance(item, dict):
            continue
        if "description" not in item:
            item_id = item.get("id", "unknown")
            item["description"] = f"Evaluation criterion: {item_id}"
            logger.warning(
                "%s: added missing 'description' to checklist item %d (id=%s)",
                node_name or "repair_checklist_structure", i, item_id,
            )
        if "partial" not in item:
            item["partial"] = False
    return data


def coerce_assessment_partial_alias(data: Any) -> Any:
    """Coerce a top-level 'Partial' ``overall_verdict`` alias to ``Yes`` (partial handled
    by the row-level flag). Runs after :func:`unwrap_wrapped_assessment` so the verdict is
    read at the correct level."""
    if isinstance(data, dict):
        verdict, was_partial = coerce_partial_verdict(data.get("overall_verdict"))
        if was_partial:
            data = dict(data)
            data["overall_verdict"] = verdict
    return data


# ---------------------------------------------------------------------------
# Model-keyed repair pipeline
# ---------------------------------------------------------------------------


def _repair_test_case_assessment(data: Any) -> Any:
    data = unwrap_wrapped_assessment(data)
    data = coerce_assessment_partial_alias(data)
    data = normalize_assessment_decomposed_requirements(data)
    data = repair_checklist_structure(data, "TestCaseAssessment")
    return data


# Dispatched by ``response_model.__name__`` in BaseLLMNode._parse_llm_response.
# Each value is an ordered list of repair callables ``(dict) -> dict``.
REPAIRS_BY_MODEL: Dict[str, List[Callable[[Any], Any]]] = {
    "DecomposedRequirement": [rehome_decomposed_specs],
    "TestCaseAssessment": [_repair_test_case_assessment],
    # Order matters: lift a list out of a wrapper first, then wrap a still-bare object.
    "SummarizedTestCaseList": [unwrap_summarized_test_case_list, wrap_bare_summarized_test_case],
}


def apply_repairs(response_model: Any, py_obj: Any) -> Any:
    """Apply the registered repair pipeline for ``response_model`` to ``py_obj``.

    Keyed by ``response_model.__name__``; a model with no registered repairs is a no-op.
    Individual repair failures are swallowed (best-effort) so a repair bug can never turn
    a recoverable payload into a crash — validation still runs on whatever we return.
    """
    name = getattr(response_model, "__name__", "")
    repairs = REPAIRS_BY_MODEL.get(name)
    if not repairs:
        return py_obj
    for repair in repairs:
        try:
            py_obj = repair(py_obj)
        except Exception as e:  # pragma: no cover - defensive; repairs are pure
            logger.warning("apply_repairs: %s repair %s failed — %s", name, getattr(repair, "__name__", repair), e)
    return py_obj