"""
Node implementations for the single-test-case reviewer.

Pipeline shape (v3 prompts onwards — only the coverage axis fans out per spec):

    decomposer (sequential loop over requirements)
        -> coverage_router (sync no-op)
            -> dispatch_coverage    -> coverage_evaluator   x N (per spec)
            -> (direct edge)        -> logical_evaluator    x 1 (test-case-level)
            -> (direct edge)        -> prereqs_evaluator    x 1 (test-case-level)
                -> aggregator
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, List, Optional

import yaml
from langgraph.types import Send

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.agents.shared.nodes import (
    StandardLLMNode,
    DecomposerNode,
    make_decomposer_node,
)
from qaai.core.cache import ReviewCacheManager
from qaai.core.config import settings
from qaai.utils import render_prompt

from .core import (
    DecomposedRequirement,
    OverallAnalysis,
    ReviewObjective,
    SpecAnalysis,
    TCReviewState,
    TestCaseAssessment,
)

logger = logging.getLogger(__name__)


def validate_tc_inputs(state: TCReviewState) -> List[str]:
    """Input-gate check for the single-test-case reviewer.

    A review is meaningful only with at least one traced upstream requirement
    and a test case that has step text to evaluate. Returns the labels of
    missing inputs; an empty list means the graph proceeds normally.
    See qaai.agents.shared.gate.
    """
    missing: List[str] = []
    if not state.get("requirements"):
        missing.append("requirements")
    test_case = state.get("test_case")
    steps = getattr(test_case, "steps", None) if test_case else None
    if not (steps and str(steps).strip()):
        missing.append("test_case_steps")
    return missing


# ---------------------------------------------------------------------------
# Review objectives loader
# ---------------------------------------------------------------------------

_DEFAULT_OBJECTIVES_PATH = Path(__file__).parent / "review_objectives.yaml"


def load_default_review_objectives(path: Optional[Path] = None) -> List[ReviewObjective]:
    """
    Load the default review-objectives checklist from review_objectives.yaml.

    Returns ReviewObjective instances with empty `assessment` strings; the
    aggregator populates them.
    """
    yaml_path = Path(path) if path is not None else _DEFAULT_OBJECTIVES_PATH
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [
        ReviewObjective(
            id=item["id"],
            description=" ".join(item["description"].split()),
            mandatory=item.get("mandatory", True),  # Default to True for backward compatibility
        )
        for item in data
    ]


# ---------------------------------------------------------------------------
# Decomposer (sequential loop over requirements)
# ---------------------------------------------------------------------------


class TCDecomposerNode:
    """
    Wraps the shared DecomposerNode with a sequential loop over the
    `requirements` list on TCReviewState. One LLM call per requirement;
    results accumulated into a single list before returning to the graph.
    """

    def __init__(self, inner: DecomposerNode):
        self._inner = inner

    def _validate_state(self, state: TCReviewState) -> bool:
        reqs = state.get("requirements")
        return reqs is not None and len(reqs) > 0

    async def __call__(self, state: TCReviewState) -> dict:
        if not self._validate_state(state):
            logger.debug("TCDecomposerNode: skipping — no requirements in state")
            return {"decomposed_requirements": None}

        cache_mode = state.get("cache_mode", "partial")
        reqs = state["requirements"]
        # Decompose all requirements concurrently (was a serial await-loop, the
        # main latency bottleneck on this path). Order is preserved by gather.
        inner_updates = await asyncio.gather(*(
            self._inner({"requirement": req, "cache_mode": cache_mode})
            for req in reqs
        ))

        results: List[DecomposedRequirement] = []
        for req, inner_update in zip(reqs, inner_updates):
            decomposed = inner_update.get("decomposed_requirement")
            if decomposed is not None:
                results.append(decomposed)
            else:
                logger.warning(
                    "TCDecomposerNode: decomposition failed for requirement %s",
                    getattr(req, "req_id", None),
                )

        return {"decomposed_requirements": results if results else None}


def make_tc_decomposer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: Optional[str] = None,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> TCDecomposerNode:
    """Build a TCDecomposerNode that wraps the shared DecomposerNode.

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.decomposer
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.decomposer

    inner = make_decomposer_node(
        client=client,
        model=model,
        model_kwargs=model_kwargs,
        prompt_template=prompt_template,
        cache_manager=cache_manager,
        **template_vars,
    )
    return TCDecomposerNode(inner=inner)


# ---------------------------------------------------------------------------
# Per-axis single-spec evaluators (Send fan-out targets)
# ---------------------------------------------------------------------------


class _SingleSpecAxisNode(StandardLLMNode):
    """
    Common base for the three axis evaluators. Each axis differs only by
    the state field it writes into; behavior is otherwise identical:
    payload = {test_case, requirement, decomposed_spec}; response is a single
    SpecAnalysis appended via the operator.add reducer on TCReviewState.

    The cache → LLM → cache flow is inherited from StandardLLMNode; this base
    only supplies the per-spec cache-node-name, the payload, and the
    list-wrapped state update (keyed by OUTPUT_KEY) for the operator.add reducer.
    """

    OUTPUT_KEY: str = ""

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        system_prompt: str,
        model_kwargs: dict | None = None,
        cache_manager: Optional[Any] = None,
        prompt_version: str = "",
        is_final_output: bool = False,
        prompt_set: Optional[str] = None,
    ):
        # response_model is fixed for every axis (mirrors HazardEvaluatorNode).
        super().__init__(
            client, model, SpecAnalysis, system_prompt, model_kwargs,
            cache_manager, prompt_version, is_final_output, prompt_set=prompt_set,
        )

    def _validate_state(self, state: Any) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirement") is not None,
            state.get("decomposed_spec") is not None,
        ])

    def _get_skip_response(self) -> dict:
        return {self.OUTPUT_KEY: []}

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _get_cache_node_name(self, state: Any = None) -> str:
        # One file per spec under the test case's folder — disambiguate by
        # spec_id so parallel per-spec evaluations don't clobber one key.
        spec = state.get("decomposed_spec") if state else None
        spec_id = getattr(spec, "spec_id", "") if spec else ""
        return f"{self.__class__.__name__.lower()}_{spec_id}"

    def _build_payload(self, state: Any) -> dict:
        """Payload sent to the LLM. Per-spec by default; the no-decomposition
        variant overrides this to judge the requirement directly (no spec)."""
        return {
            "test_case": state["test_case"].model_dump(),
            "requirement": state["requirement"].model_dump(),
            "decomposed_spec": state["decomposed_spec"].model_dump(),
        }

    def _format_response(self, parsed_result: Any) -> dict:
        # List-wrapped for the operator.add reducer on OUTPUT_KEY.
        return {self.OUTPUT_KEY: [parsed_result]}


class SingleSpecCoverageNode(_SingleSpecAxisNode):
    """Coverage axis evaluator — fan-out target, one Send per decomposed spec."""
    OUTPUT_KEY = "coverage_analysis"


class SingleReqCoverageNode(_SingleSpecAxisNode):
    """No-decomposition coverage evaluator — fan-out target, one Send per
    requirement. Judges the test case directly against the original requirement
    text (no decomposed_spec). Emits one SpecAnalysis per requirement with
    spec_id == req_id, so the downstream aggregator's count logic is unchanged."""
    OUTPUT_KEY = "coverage_analysis"

    def _validate_state(self, state: Any) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirement") is not None,
        ])

    def _get_cache_node_name(self, state: Any = None) -> str:
        # One file per requirement under the test case's folder.
        req = state.get("requirement") if state else None
        req_id = getattr(req, "req_id", "") if req else ""
        return f"{self.__class__.__name__.lower()}_{req_id}"

    def _build_payload(self, state: Any) -> dict:
        return {
            "test_case": state["test_case"].model_dump(),
            "requirement": state["requirement"].model_dump(),
        }


def _make_axis_node(
    node_cls: type,
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> _SingleSpecAxisNode:
    system_prompt = render_prompt(prompt_template, **template_vars)
    return node_cls(
        client=client,
        model=model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


# ---------------------------------------------------------------------------
# Test-case-level axis evaluators (single LLM call each, no Send fan-out).
# Logical-structure and prereqs are properties of the test case as a whole;
# they do not iterate over decomposed specs from v3 onwards.
# ---------------------------------------------------------------------------


class OverallLogicalNode(StandardLLMNode):
    """Logical-structure axis — single test-case-level LLM call. No spec iteration."""

    def _validate_state(self, state: TCReviewState) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirements") is not None,
        ])

    def _get_cache_entity_id(self, state: TCReviewState) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _build_payload(self, state: TCReviewState) -> dict:
        return {
            "test_case": state["test_case"].model_dump(),
            "requirements": [r.model_dump() for r in state["requirements"]],
        }

    def _format_response(self, parsed_result: Optional[OverallAnalysis]) -> dict:
        return {"logical_structure_analysis": parsed_result}

    def _get_skip_response(self) -> dict:
        return {"logical_structure_analysis": None}


class OverallPrereqsNode(StandardLLMNode):
    """Prereqs axis — single test-case-level LLM call. No spec iteration."""

    def _validate_state(self, state: TCReviewState) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirements") is not None,
        ])

    def _get_cache_entity_id(self, state: TCReviewState) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _build_payload(self, state: TCReviewState) -> dict:
        return {
            "test_case": state["test_case"].model_dump(),
            "requirements": [r.model_dump() for r in state["requirements"]],
        }

    def _format_response(self, parsed_result: Optional[OverallAnalysis]) -> dict:
        return {"prereqs_analysis": parsed_result}

    def _get_skip_response(self) -> dict:
        return {"prereqs_analysis": None}


def make_coverage_single_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: Optional[str] = None,
    cache_manager: Optional[Any] = None,
    include_decomposition: bool = True,
    **template_vars,
) -> _SingleSpecAxisNode:
    """Build the coverage evaluator node.

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.single_test_coverage_eval
        include_decomposition: when True (default) the coverage axis fans out per
            decomposed spec (SingleSpecCoverageNode); when False it fans out per
            requirement and judges the original requirement text directly
            (SingleReqCoverageNode, no decomposed_spec input).
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.single_test_coverage_eval

    node_cls = SingleSpecCoverageNode if include_decomposition else SingleReqCoverageNode
    return _make_axis_node(
        node_cls, client, model, model_kwargs, prompt_template,
        cache_manager=cache_manager, **template_vars,
    )


def make_logical_single_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: Optional[str] = None,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> OverallLogicalNode:
    """Build the test-case-level logical-structure node (single LLM call, no Send).

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.single_test_logical_steps
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.single_test_logical_steps

    system_prompt = render_prompt(prompt_template, **template_vars)
    return OverallLogicalNode(
        client=client,
        model=model,
        response_model=OverallAnalysis,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
    )


def make_prereqs_single_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: Optional[str] = None,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> OverallPrereqsNode:
    """Build the test-case-level prereqs node (single LLM call, no Send).

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.single_test_prereqs
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.single_test_prereqs

    system_prompt = render_prompt(prompt_template, **template_vars)
    return OverallPrereqsNode(
        client=client,
        model=model,
        response_model=OverallAnalysis,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
    )


# ---------------------------------------------------------------------------
# Send dispatcher (coverage axis only — the only axis that fans out per spec)
# ---------------------------------------------------------------------------


def dispatch_coverage(state: TCReviewState) -> List[Send]:
    """Emit one Send per (decomposed_requirement, decomposed_spec) pair to the
    coverage_evaluator. The logical and prereqs axes do NOT fan out per spec
    from v3 onwards — they take the full state via direct edges."""
    test_case = state.get("test_case")
    decomposed_reqs = state.get("decomposed_requirements")
    if not test_case or not decomposed_reqs:
        logger.warning("dispatch_coverage: incomplete state, skipping fan-out")
        return []

    cache_mode = state.get("cache_mode", "partial")
    return [
        Send("coverage_evaluator", {
            "test_case": test_case,
            "requirement": dr.requirement,
            "decomposed_spec": spec,
            "cache_mode": cache_mode,
        })
        for dr in decomposed_reqs
        for spec in dr.decomposed_specifications
    ]


def dispatch_coverage_by_requirement(state: TCReviewState) -> List[Send]:
    """No-decomposition fan-out: emit one Send per requirement to the
    coverage_evaluator, judging the test case against the original requirement
    text (no decomposed_spec). Mirrors dispatch_coverage's safe-no-op contract."""
    test_case = state.get("test_case")
    requirements = state.get("requirements")
    if not test_case or not requirements:
        logger.warning("dispatch_coverage_by_requirement: incomplete state, skipping fan-out")
        return []

    cache_mode = state.get("cache_mode", "partial")
    return [
        Send("coverage_evaluator", {
            "test_case": test_case,
            "requirement": requirement,
            "cache_mode": cache_mode,
        })
        for requirement in requirements
    ]


# ---------------------------------------------------------------------------
# Aggregator (synthesis across the three axis lists)
# ---------------------------------------------------------------------------


class AggregatorNode(StandardLLMNode):
    """Synthesizes the three per-axis SpecAnalysis lists into a TestCaseAssessment."""

    def _validate_state(self, state: TCReviewState) -> bool:
        # decomposed_requirements is intentionally NOT required: the
        # no-decomposition mode (test_case_reviewer_v3) never produces it.
        return all([
            state.get("test_case") is not None,
            state.get("requirements") is not None,
            state.get("review_objectives") is not None,
        ])

    def _get_cache_entity_id(self, state: TCReviewState) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _build_payload(self, state: TCReviewState) -> dict:
        logical = state.get("logical_structure_analysis")
        prereqs = state.get("prereqs_analysis")
        # Absent in no-decomposition mode — the v7 aggregator prompt omits it.
        decomposed = state.get("decomposed_requirements") or []
        return {
            "test_case": state["test_case"].model_dump(),
            "requirements": [r.model_dump() for r in state["requirements"]],
            "decomposed_requirements": [d.model_dump() for d in decomposed],
            "coverage_analysis": [a.model_dump() for a in state.get("coverage_analysis", [])],
            "logical_structure_analysis": logical.model_dump() if logical is not None else None,
            "prereqs_analysis": prereqs.model_dump() if prereqs is not None else None,
            "review_objectives": [o.model_dump() for o in state["review_objectives"]],
        }

    def _format_response(self, parsed_result: Optional[TestCaseAssessment]) -> dict:
        return {"aggregated_assessment": parsed_result}

    def _get_skip_response(self) -> dict:
        return {"aggregated_assessment": None}


def make_aggregator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: Optional[str] = None,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> AggregatorNode:
    """Build the aggregator node.

    This is the graph's FINAL output node: under "partial" caching it always
    re-runs so the user gets a fresh assessment from cached interim results.

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.single_test_aggregator
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.single_test_aggregator

    system_prompt = render_prompt(prompt_template, **template_vars)
    return AggregatorNode(
        client=client,
        model=model,
        response_model=TestCaseAssessment,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        is_final_output=True,
    )
