"""
Node implementations for RTM review agent (test suite reviewer).

Generic base classes and the reusable DecomposerNode/make_decomposer_node
are imported from autoqa.components.shared.nodes. This module defines the
suite-specific nodes: SummaryNode, SingleSpecEvaluatorNode,
and SynthesizerNode, plus the dispatch_coverage Send fan-out.
"""
import json
from typing import Optional, List, Any
from langgraph.types import Send
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.utils import render_prompt
from autoqa.prj_logger import ProjectLogger
from autoqa.core.config import settings
from autoqa.components.shared.nodes import (
    BaseLLMNode,
    StandardLLMNode,
    DecomposerNode,
    make_decomposer_node,
    sanitize_requirement_text,
)

project_logger = ProjectLogger(name="logger.nodes", log_file=settings.log_file_path)
project_logger.config()
logger = project_logger.get_logger()
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


class SummaryNode(BaseLLMNode):
    """Summarizes raw test cases into structured format with batching support.
    
    For v4+ prompts: LLM returns only the summary array; this node reconstructs
    the full TestSuite by combining the summary with the original requirement
    and test_cases from state.
    
    Implements batching to handle 100+ test cases without hitting token limits.
    Batch size is configurable via BATCH_SIZE class attribute.
    """

    BATCH_SIZE = 25  # Tune based on model output limits (25 works well for Haiku)

    def __init__(self, client: RateLimitOpenAIClient, model: str, response_model, system_prompt: str, model_kwargs: dict | None = None):
        super().__init__(client, model, system_prompt, model_kwargs)
        self.response_model = response_model

    def _validate_state(self, state: RTMReviewState) -> bool:
        # Validate both requirement and test_cases exist
        return (
            state.get("requirement") is not None 
            and state.get("test_cases") is not None
        )

    def _build_payload(self, requirement, test_cases: List) -> dict:
        """Build payload for a batch of test cases."""
        # Sanitize requirement text to prevent JSON parsing failures
        # from unescaped quotes, HTML entities, and excessive length
        sanitized_text = sanitize_requirement_text(text=requirement.text, req_id=requirement.req_id)
        
        # Include requirement in the payload to match prompt schema
        return {
            "requirement": {
                "req_id": requirement.req_id,
                "text": sanitized_text,
            },
            "test_cases": [
                {
                    "test_id": tc.test_id,
                    "description": tc.description,
                    "setup": tc.setup,
                    "steps": tc.steps,
                    "expectedResults": tc.expectedResults
                }
                for tc in test_cases
            ]
        }

    async def __call__(self, state: RTMReviewState) -> RTMReviewState:
        """Process test cases in batches and accumulate summaries."""
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return {"test_suite": None}
        
        requirement = state.get("requirement")
        test_cases = state.get("test_cases")
        
        if not test_cases:
            logger.warning("%s: no test cases to summarize", self.__class__.__name__)
            return {"test_suite": None}
        
        # Process in batches
        all_summaries = []
        num_batches = (len(test_cases) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        
        logger.info("%s: processing %d test cases in %d batches (batch_size=%d)",
                   self.__class__.__name__, len(test_cases), num_batches, self.BATCH_SIZE)
        
        for i in range(0, len(test_cases), self.BATCH_SIZE):
            batch = test_cases[i:i+self.BATCH_SIZE]
            batch_num = i // self.BATCH_SIZE + 1
            logger.info("%s: processing batch %d/%d (%d test cases)",
                       self.__class__.__name__, batch_num, num_batches, len(batch))
            
            # Build payload for this batch
            payload = self._build_payload(requirement, batch)
            
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ]
            
            result = await self.client.chat_completion(
                model=self.model,
                messages=messages,
                **self.model_kwargs,
            )
            
            parsed = self._parse_llm_response(
                result, self.response_model, self.__class__.__name__
            )
            
            if parsed is None:
                logger.warning(
                    "%s: batch %d/%d failed to parse, skipping",
                    self.__class__.__name__, batch_num, num_batches
                )
                continue
            
            # Unwrap if using SummarizedTestCaseList wrapper (v4+ prompts);
            # v2/v3 prompts return a TestSuite which is directly iterable
            all_summaries.extend(parsed.root if isinstance(parsed, SummarizedTestCaseList) else parsed)
            logger.info("%s: batch %d/%d completed, accumulated %d summaries so far",
                       self.__class__.__name__, batch_num, num_batches, len(all_summaries))
        
        # Verify we got summaries for all test cases
        if len(all_summaries) != len(test_cases):
            logger.warning(
                "%s: summary count mismatch: expected %d, got %d",
                self.__class__.__name__, len(test_cases), len(all_summaries)
            )
        
        # Reconstruct full TestSuite with all summaries
        test_suite = TestSuite(
            requirement=requirement,
            test_cases=test_cases,
            summary=all_summaries
        )
        
        logger.info("%s: completed processing %d test cases, generated %d summaries",
                   self.__class__.__name__, len(test_cases), len(all_summaries))
        
        return {"test_suite": test_suite}


class DesignSummarizerNode(BaseLLMNode):
    """Summarizes design documents into structured format with batching support.
    
    For v1+ prompts: LLM returns only the summary array; this node reconstructs
    the full list by combining the summary with the original requirement and
    design_docs from state.
    
    Implements batching to handle many design documents without hitting token limits.
    Batch size is configurable via BATCH_SIZE class attribute.
    """

    BATCH_SIZE = 5  # Default to 5 per call as specified

    def __init__(self, client: RateLimitOpenAIClient, model: str, response_model, 
                 system_prompt: str, model_kwargs: dict | None = None):
        super().__init__(client, model, system_prompt, model_kwargs)
        self.response_model = response_model

    def _validate_state(self, state: RTMReviewState) -> bool:
        # Only validate if design_docs exist; if not, skip gracefully
        return (
            state.get("requirement") is not None
            and bool(state.get("design_docs"))
        )

    def _build_payload(self, requirement, design_docs: List) -> dict:
        """Build payload for a batch of design documents."""
        sanitized_text = sanitize_requirement_text(
            text=requirement.text, req_id=requirement.req_id
        )
        
        return {
            "requirement": {
                "req_id": requirement.req_id,
                "text": sanitized_text,
            },
            "design_docs": [
                {
                    "doc_id": dd.doc_id,
                    "name": dd.name,
                    "description": dd.description,
                }
                for dd in design_docs
            ]
        }

    async def __call__(self, state: RTMReviewState) -> RTMReviewState:
        """Process design documents in batches and accumulate summaries."""
        if not self._validate_state(state):
            logger.debug("%s: skipping — no design docs or validation failed", 
                        self.__class__.__name__)
            return {"summarized_designs": None}
        
        requirement = state.get("requirement")
        design_docs = state.get("design_docs")
        
        # Process in batches
        all_summaries = []
        num_batches = (len(design_docs) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        
        logger.info("%s: processing %d design docs in %d batches (batch_size=%d)",
                   self.__class__.__name__, len(design_docs), num_batches, self.BATCH_SIZE)
        
        for i in range(0, len(design_docs), self.BATCH_SIZE):
            batch = design_docs[i:i+self.BATCH_SIZE]
            batch_num = i // self.BATCH_SIZE + 1
            logger.info("%s: processing batch %d/%d (%d design docs)",
                       self.__class__.__name__, batch_num, num_batches, len(batch))
            
            payload = self._build_payload(requirement, batch)
            
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ]
            
            result = await self.client.chat_completion(
                model=self.model,
                messages=messages,
                **self.model_kwargs,
            )
            
            parsed = self._parse_llm_response(
                result, self.response_model, self.__class__.__name__
            )
            
            if parsed is None:
                logger.warning(
                    "%s: batch %d/%d failed to parse, skipping",
                    self.__class__.__name__, batch_num, num_batches
                )
                continue
            
            all_summaries.extend(parsed.root if isinstance(parsed, SummarizedDesignSpecList) else parsed)
            
            logger.info("%s: batch %d/%d completed, accumulated %d summaries so far",
                       self.__class__.__name__, batch_num, num_batches, len(all_summaries))
        
        # Verify we got summaries for all design docs
        if len(all_summaries) != len(design_docs):
            logger.warning(
                "%s: summary count mismatch: expected %d, got %d",
                self.__class__.__name__, len(design_docs), len(all_summaries)
            )
        
        logger.info("%s: completed processing %d design docs, generated %d summaries",
                   self.__class__.__name__, len(design_docs), len(all_summaries))
        
        return {"summarized_designs": all_summaries}


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
    return [
        Send("spec_evaluator", {
            "requirement": requirement,
            "decomposed_spec": spec,
            "test_suite": test_suite,
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

    async def __call__(self, state: Any) -> RTMReviewState:
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return {"coverage_analysis": []}

        requirement = state["requirement"]
        spec = state["decomposed_spec"]
        test_suite = state["test_suite"]

        payload = {
            "original_requirement": requirement.model_dump(),
            "decomposed_spec": spec.model_dump(),
            "test_suite": test_suite.model_dump(),
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
        return {"coverage_analysis": [parsed]} if parsed else {"coverage_analysis": []}


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


# Factory functions

def make_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "summarizer/v4.0.0/template.jinja2",
    **template_vars,
) -> SummaryNode:
    """
    Create a SummaryNode with prompt loaded from Jinja2 template.

    Args:
        client: RateLimitOpenAIClient instance
        model: Model identifier string
        prompt_template: Filename of the Jinja2 template to render as the system prompt
        **template_vars: Optional variables to pass to the Jinja2 template

    Returns:
        SummaryNode: Configured summarizer node
        
    Note:
        For v4+ prompts (summarizer-v4.jinja2 and later), the response_model is
        List[SummarizedTestCase] since the LLM returns only the summary array.
        For v2/v3 prompts, the response_model is TestSuite (full object with
        requirement, test_cases, and summary).
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    
    # Determine response model based on prompt version
    # v4+ prompts return only the summary array, wrapped in SummarizedTestCaseList
    # for proper Pydantic validation
    if any(v in prompt_template for v in ("v4", "v5", "v6")):
        response_model = SummarizedTestCaseList
    else:
        # v2/v3 prompts return the full TestSuite object
        response_model = TestSuite
    
    return SummaryNode(
        client=client,
        model=model,
        response_model=response_model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs
    )


def make_coverage_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "coverage_evaluator/v7.0.0/template.jinja2",
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
        model_kwargs=model_kwargs
    )


def make_synthesizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "synthesizer/v8.0.0/template.jinja2",
    **template_vars,
) -> SynthesizerNode:
    """
    Create a SynthesizerNode (MoA-inspired) that synthesizes coverage evaluations
    into a single holistic assessment of requirement coverage.

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
        model_kwargs=model_kwargs
    )


def make_design_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "design_summarizer/v1.0.0/template.jinja2",
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
        model_kwargs=model_kwargs
    )
