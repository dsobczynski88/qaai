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
import json
import logging
from pathlib import Path
from typing import Any, List, Optional

import yaml
from langgraph.types import Send

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.shared.nodes import (
    BaseLLMNode,
    StandardLLMNode,
    DecomposerNode,
    make_decomposer_node,
)
from autoqa.core.cache import ReviewCacheManager
from autoqa.core.config import settings
from autoqa.utils import render_prompt

from .core import (
    DecomposedRequirement,
    OverallAnalysis,
    ReviewObjective,
    SpecAnalysis,
    TCReviewState,
    TestCaseAssessment,
)

logger = logging.getLogger(__name__)


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
        results: List[DecomposedRequirement] = []
        for req in state["requirements"]:
            inner_update = await self._inner({"requirement": req, "cache_mode": cache_mode})
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


class _SingleSpecAxisNode(BaseLLMNode):
    """
    Common base for the three axis evaluators. Each axis differs only by
    the state field it writes into; behavior is otherwise identical:
    payload = {test_case, requirement, decomposed_spec}; response is a single
    SpecAnalysis appended via the operator.add reducer on TCReviewState.
    """

    OUTPUT_KEY: str = ""

    def _validate_state(self, state: Any) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirement") is not None,
            state.get("decomposed_spec") is not None,
        ])

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _cache_node_name(self, state: Any) -> str:
        # One file per spec under the test case's folder — disambiguate by
        # spec_id so parallel per-spec evaluations don't clobber one key.
        spec = state.get("decomposed_spec")
        spec_id = getattr(spec, "spec_id", "") if spec else ""
        return f"{self.__class__.__name__.lower()}_{spec_id}"

    async def __call__(self, state: Any) -> dict:
        if not self.OUTPUT_KEY:
            raise RuntimeError(f"{self.__class__.__name__}: OUTPUT_KEY must be set")
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return {self.OUTPUT_KEY: []}

        entity_id = self._get_cache_entity_id(state)
        node_name = self._cache_node_name(state)

        # --- Tier 2/3: cache check ---
        if self._cache_read_allowed(state) and entity_id:
            cached = await self.cache_manager.get(entity_id, node_name, self.prompt_version)
            if cached is not None:
                try:
                    return {self.OUTPUT_KEY: [SpecAnalysis.model_validate(cached["result"])]}
                except Exception as e:
                    logger.warning("%s: cache restore failed, re-running — %s", self.__class__.__name__, e)

        payload = {
            "test_case": state["test_case"].model_dump(),
            "requirement": state["requirement"].model_dump(),
            "decomposed_spec": state["decomposed_spec"].model_dump(),
        }
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(payload)},
        ]

        result = await self.client.chat_completion(
            model=self.model,
            messages=messages,
            **self.model_kwargs,
        )
        parsed = self._parse_llm_response(result, SpecAnalysis, self.__class__.__name__)
        if not parsed:
            return {self.OUTPUT_KEY: []}

        # --- Tier 2/3: write-through cache ---
        if self._cache_write_allowed(state) and entity_id:
            usage = getattr(result, "usage", None)
            try:
                await self.cache_manager.set(
                    entity_id=entity_id,
                    node_name=node_name,
                    prompt_version=self.prompt_version,
                    result_dict=parsed.model_dump(),
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    model=self.model,
                )
            except Exception as e:
                logger.warning("%s: cache write failed — %s", self.__class__.__name__, e)

        return {self.OUTPUT_KEY: [parsed]}


class SingleSpecCoverageNode(_SingleSpecAxisNode):
    """Coverage axis evaluator — fan-out target, one Send per decomposed spec."""
    OUTPUT_KEY = "coverage_analysis"


def _make_axis_node(
    node_cls: type,
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
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
    **template_vars,
) -> SingleSpecCoverageNode:
    """Build the coverage evaluator node.

    Args:
        prompt_template: Optional override. If None, uses settings.prompt_config.single_test_coverage_eval
    """
    if prompt_template is None:
        prompt_template = settings.prompt_config.single_test_coverage_eval

    return _make_axis_node(
        SingleSpecCoverageNode, client, model, model_kwargs, prompt_template,
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


# ---------------------------------------------------------------------------
# Aggregator (synthesis across the three axis lists)
# ---------------------------------------------------------------------------


class AggregatorNode(StandardLLMNode):
    """Synthesizes the three per-axis SpecAnalysis lists into a TestCaseAssessment."""

    def _validate_state(self, state: TCReviewState) -> bool:
        return all([
            state.get("test_case") is not None,
            state.get("requirements") is not None,
            state.get("decomposed_requirements") is not None,
            state.get("review_objectives") is not None,
        ])

    def _get_cache_entity_id(self, state: TCReviewState) -> Optional[str]:
        test_case = state.get("test_case")
        return getattr(test_case, "test_id", None) if test_case else None

    def _build_payload(self, state: TCReviewState) -> dict:
        logical = state.get("logical_structure_analysis")
        prereqs = state.get("prereqs_analysis")
        return {
            "test_case": state["test_case"].model_dump(),
            "requirements": [r.model_dump() for r in state["requirements"]],
            "decomposed_requirements": [d.model_dump() for d in state["decomposed_requirements"]],
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
