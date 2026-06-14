"""
Node implementations for RTM review agent (test suite reviewer).

Generic base classes and the reusable DecomposerNode/make_decomposer_node
are imported from qaai.agents.shared.nodes. This module defines the
suite-specific nodes: SummaryNode, DesignSummarizerNode, SingleSpecEvaluatorNode,
and SynthesizerNode, plus the dispatch_coverage Send fan-out.
"""
import json
import logging
import re
from typing import Optional, List, Any
from langgraph.types import Send
from qaai.agents.clients import RateLimitOpenAIClient
from qaai.utils import render_prompt
from qaai.core.cache import ReviewCacheManager
from qaai.core.constants import DEFAULT_BATCH_SIZE
from qaai.agents.shared.nodes import (
    BaseLLMNode,
    BatchedLLMNode,
    StandardLLMNode,
    DecomposerNode,
    make_decomposer_node,
    sanitize_requirement_text,
)

logger = logging.getLogger(__name__)
from .core import (
    RTMReviewState,
    DecomposedRequirement,
    TestSuite,
    SummarizedTestCase,
    SummarizedTestCaseList,
    SummarizedDesignSpec,
    SummarizedDesignSpecList,
    EvaluatedSpec,
    SynthesizedAssessment,
)


class SummaryNode(BatchedLLMNode):
    """Summarizes raw test cases into structured format with batching support."""

    BATCH_SIZE = DEFAULT_BATCH_SIZE  # Tune via core.constants (25 works well for Haiku)

    def _validate_state(self, state: RTMReviewState) -> bool:
        return state.get("requirement") is not None and state.get("test_cases") is not None

    def _get_cache_entity_id(self, state: RTMReviewState) -> Optional[str]:
        requirement = state.get("requirement")
        return getattr(requirement, "req_id", None) if requirement else None

    def _restore_from_cache(self, cached: dict) -> list:
        return [SummarizedTestCase.model_validate(d) for d in cached["result"]]

    def _get_items(self, state: RTMReviewState) -> list:
        return state.get("test_cases") or []

    def _build_batch_payload(self, state: RTMReviewState, batch: List) -> dict:
        requirement = state["requirement"]
        sanitized_text = sanitize_requirement_text(text=requirement.text, req_id=requirement.req_id)
        return {
            "requirement": {"req_id": requirement.req_id, "text": sanitized_text},
            "test_cases": [
                {
                    "test_id": tc.test_id,
                    "description": tc.description,
                    "setup": tc.setup,
                    "steps": tc.steps,
                    "expectedResults": tc.expectedResults,
                }
                for tc in batch
            ],
        }

    def _unwrap_batch_result(self, parsed) -> list:
        return parsed.root if isinstance(parsed, SummarizedTestCaseList) else list(parsed)

    def _get_skip_response(self) -> dict:
        return {"test_suite": None}

    def _build_result(self, state: RTMReviewState, all_summaries: list) -> dict:
        return {"test_suite": TestSuite(
            requirement=state["requirement"],
            test_cases=state["test_cases"],
            summary=all_summaries,
        )}


class DesignSummarizerNode(BatchedLLMNode):
    """Summarizes design documents into structured format with batching support.
    
    Note: This node is optional — it only runs if design_docs are present in the state.
    If no design documents are available for a requirement, the node gracefully skips
    and returns an empty summarized_designs result.
    """

    BATCH_SIZE = 5

    def _validate_state(self, state: RTMReviewState) -> bool:
        # Only require a requirement; design_docs are optional.
        # If design_docs are missing or empty, _get_items() will return [],
        # triggering the standard BatchedLLMNode empty-items handling.
        return state.get("requirement") is not None

    def _get_cache_entity_id(self, state: RTMReviewState) -> Optional[str]:
        requirement = state.get("requirement")
        return getattr(requirement, "req_id", None) if requirement else None

    def _restore_from_cache(self, cached: dict) -> list:
        return [SummarizedDesignSpec.model_validate(d) for d in cached["result"]]

    def _get_items(self, state: RTMReviewState) -> list:
        items = state.get("design_docs") or []
        if not items:
            logger.info(
                "%s: no design documents for requirement %s (optional)",
                self.__class__.__name__,
                getattr(state.get("requirement"), "req_id", "unknown")
            )
        return items

    def _build_batch_payload(self, state: RTMReviewState, batch: List) -> dict:
        requirement = state["requirement"]
        sanitized_text = sanitize_requirement_text(text=requirement.text, req_id=requirement.req_id)
        return {
            "requirement": {"req_id": requirement.req_id, "text": sanitized_text},
            "design_docs": [
                {"doc_id": dd.doc_id, "name": dd.name, "description": dd.description}
                for dd in batch
            ],
        }

    def _unwrap_batch_result(self, parsed) -> list:
        return parsed.root if isinstance(parsed, SummarizedDesignSpecList) else list(parsed)

    def _get_skip_response(self) -> dict:
        return {"summarized_designs": None}

    def _build_result(self, state: RTMReviewState, all_summaries: list) -> dict:
        # Only include summarized_designs if we actually have summaries.
        # If design_docs were empty or missing, all_summaries will be empty
        # and we return None (consistent with _get_skip_response).
        return {"summarized_designs": all_summaries if all_summaries else None}


def dispatch_coverage(state: RTMReviewState) -> List[Send]:
    """
    LangGraph Send dispatcher: fans out one Send per decomposed spec so that
    each spec is evaluated in parallel by SingleSpecEvaluatorNode.
    Returns an empty list if required state keys are missing (safe no-op).
    """
    requirement = state.get("requirement")
    decomposed = state.get("decomposed_requirement")
    test_suite = state.get("test_suite")
    if not requirement or not decomposed or not test_suite:
        logger.warning("dispatch_coverage: incomplete state, skipping fan-out")
        return []
    cache_mode = state.get("cache_mode", "partial")
    # summarized_designs joined at coverage_router (may be None when a
    # requirement has no design docs). Send only forwards the keys placed in
    # this dict, so it must be threaded through explicitly to reach spec_evaluator.
    summarized_designs = state.get("summarized_designs")
    return [
        Send("spec_evaluator", {
            "requirement": requirement,
            "decomposed_spec": spec,
            "test_suite": test_suite,
            "summarized_designs": summarized_designs,
            "cache_mode": cache_mode,
        })
        for spec in decomposed.decomposed_specifications
    ]


class SingleSpecEvaluatorNode(BaseLLMNode):
    """
    Evaluates coverage for a single decomposed spec (one LLM call).
    Invoked in parallel via the LangGraph Send API — dispatch_coverage()
    creates one Send per spec; results are accumulated by the operator.add
    reducer on RTMReviewState.coverage_analysis.
    """

    def _validate_state(self, state: Any) -> bool:
        return all((
            state.get("requirement") is not None,
            state.get("decomposed_spec") is not None,
            state.get("test_suite") is not None,
        ))

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        requirement = state.get("requirement")
        return getattr(requirement, "req_id", None) if requirement else None

    def _cache_node_name(self, state: Any) -> str:
        # One file per spec under the requirement's folder — disambiguate by
        # spec_id so parallel per-spec evaluations don't clobber one key.
        spec = state.get("decomposed_spec")
        spec_id = getattr(spec, "spec_id", "") if spec else ""
        return f"singlespecevaluatornode_{spec_id}"

    async def __call__(self, state: Any) -> RTMReviewState:
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return {"coverage_analysis": []}

        requirement = state["requirement"]
        spec = state["decomposed_spec"]
        test_suite = state["test_suite"]
        summarized_designs = state.get("summarized_designs")

        entity_id = self._get_cache_entity_id(state)
        node_name = self._cache_node_name(state)

        # --- Tier 2/3: cache check ---
        if self._cache_read_allowed(state) and entity_id:
            cached = await self.cache_manager.get(entity_id, node_name, self.prompt_version)
            if cached is not None:
                try:
                    return {"coverage_analysis": [EvaluatedSpec.model_validate(cached["result"])]}
                except Exception as e:
                    logger.warning("%s: cache restore failed, re-running — %s", self.__class__.__name__, e)

        payload = {
            "original_requirement": requirement.model_dump(),
            "decomposed_spec": spec.model_dump(),
            "test_suite": test_suite.model_dump(),
            # Optional supporting context for the R6-style design alignment lens.
            # Null-safe shape mirrors SynthesizerNode._build_payload.
            "summarized_designs": (
                [s.model_dump() for s in summarized_designs] if summarized_designs else None
            ),
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
        parsed = self._parse_llm_response(result, EvaluatedSpec, self.__class__.__name__)
        if not parsed:
            return {"coverage_analysis": []}

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

        return {"coverage_analysis": [parsed]}


class SynthesizerNode(StandardLLMNode):
    """MoA-inspired node that synthesizes coverage evaluations into a holistic assessment."""

    def _validate_state(self, state: RTMReviewState) -> bool:
        coverage_analysis = state.get("coverage_analysis")
        return all((
            state.get("requirement") is not None,
            state.get("decomposed_requirement") is not None,
            state.get("test_suite") is not None,
            bool(coverage_analysis),
        ))

    def _get_cache_entity_id(self, state: RTMReviewState) -> Optional[str]:
        requirement = state.get("requirement")
        return getattr(requirement, "req_id", None) if requirement else None

    def _build_payload(self, state: RTMReviewState) -> dict:
        requirement = state.get("requirement")
        decomposed_requirement = state.get("decomposed_requirement")
        test_suite = state.get("test_suite")
        coverage_analysis = state.get("coverage_analysis")
        summarized_designs = state.get("summarized_designs")

        payload = {
            "requirement": requirement.model_dump(),
            "decomposed_specifications": [
                s.model_dump() for s in decomposed_requirement.decomposed_specifications
            ],
            "summarized_test_cases": [
                s.model_dump() for s in test_suite.summary
            ],
            "coverage_evaluations": [
                e.model_dump() for e in coverage_analysis
            ],
            # Include summarized_designs if present (for R6 Design Alignment criterion)
            "summarized_designs": (
                [s.model_dump() for s in summarized_designs] if summarized_designs else None
            ),
        }

        return payload

    def _format_response(self, parsed_result: Optional[SynthesizedAssessment]) -> RTMReviewState:
        return {"synthesized_assessment": parsed_result}

    def _get_skip_response(self) -> RTMReviewState:
        return {"synthesized_assessment": None}


def _prompt_major_version(template_path: str) -> int:
    """Extract the major version number from a template path like 'summarizer/v4.0.0/template.jinja2'."""
    m = re.search(r"/v(\d+)\.", template_path)
    return int(m.group(1)) if m else 0


# Factory functions

def make_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> SummaryNode:
    """
    Create a SummaryNode with prompt loaded from Jinja2 template.

    v4+ prompts return only the summary array (SummarizedTestCaseList).
    v2/v3 prompts return the full TestSuite object.
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    response_model = SummarizedTestCaseList if _prompt_major_version(prompt_template) >= 4 else TestSuite
    return SummaryNode(
        client=client,
        model=model,
        response_model=response_model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
    )


def make_coverage_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> SingleSpecEvaluatorNode:
    """
    Create a SingleSpecEvaluatorNode for per-spec coverage evaluation.

    Used with LangGraph's Send API: dispatch_coverage() fans out one Send per
    decomposed spec; each invocation of this node handles exactly one spec.

    Args:
        client: RateLimitOpenAIClient instance
        model: Model identifier string
        prompt_template: Filename of the Jinja2 template to render as the system prompt
        **template_vars: Optional variables to pass to the Jinja2 template

    Returns:
        SingleSpecEvaluatorNode: Configured single-spec evaluator node
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    return SingleSpecEvaluatorNode(
        client=client,
        model=model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
    )


def make_synthesizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> SynthesizerNode:
    """
    Create a SynthesizerNode (MoA-inspired) that synthesizes coverage evaluations
    into a single holistic assessment of requirement coverage.

    This is the graph's FINAL output node: under "partial" caching it always
    re-runs so the user gets a fresh assessment from cached interim results.

    Args:
        client: RateLimitOpenAIClient instance
        model: Model identifier string
        prompt_template: Filename of the Jinja2 template to render as the system prompt
        **template_vars: Optional variables to pass to the Jinja2 template

    Returns:
        SynthesizerNode: Configured synthesizer node
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    return SynthesizerNode(
        client=client,
        model=model,
        response_model=SynthesizedAssessment,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        is_final_output=True,
    )


def make_design_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    **template_vars,
) -> DesignSummarizerNode:
    """Create a DesignSummarizerNode with prompt loaded from Jinja2 template.
    
    Args:
        client: RateLimitOpenAIClient instance
        model: Model identifier string
        model_kwargs: Model-specific keyword arguments
        prompt_template: Filename of the Jinja2 template to render as the system prompt
        **template_vars: Optional variables to pass to the Jinja2 template

    Returns:
        DesignSummarizerNode: Configured design summarizer node
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    response_model = SummarizedDesignSpecList

    return DesignSummarizerNode(
        client=client,
        model=model,
        response_model=response_model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
    )
