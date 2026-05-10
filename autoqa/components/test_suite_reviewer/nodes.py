"""
Node implementations for RTM review agent (test suite reviewer).

Generic base classes and the reusable DecomposerNode/make_decomposer_node
are imported from autoqa.components.shared.nodes. This module defines the
suite-specific nodes: SummaryNode, TestGeneratorNode, SingleSpecEvaluatorNode,
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
            
            # Unwrap if using SummarizedTestCaseList wrapper (v4+ prompts)
            if isinstance(parsed, SummarizedTestCaseList):
                all_summaries.extend(parsed.root)
            else:
                # For backward compatibility with v2/v3 prompts that return TestSuite
                all_summaries.extend(parsed)
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
        return all([
            state.get("requirement") is not None,
            state.get("decomposed_spec") is not None,
            state.get("test_suite") is not None,
        ])

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


class TestGeneratorNode(StandardLLMNode):
    """Generates adversarial test cases to fill coverage gaps."""

    def _validate_state(self, state: RTMReviewState) -> bool:
        decomposed = state.get("decomposed_requirement")
        test_suite = state.get("test_suite")
        return decomposed is not None and test_suite is not None

    def _build_payload(self, state: RTMReviewState) -> dict:
        decomposed_requirement = state.get("decomposed_requirement")
        test_suite = state.get("test_suite")
        assert decomposed_requirement is not None
        assert test_suite is not None
        return {
            "decomposed_requirement": decomposed_requirement.model_dump(),
            "test_suite": test_suite.model_dump(),
        }

    def _format_response(self, parsed_result: Optional[TestSuite]) -> RTMReviewState:
        return {"test_suite": parsed_result}

    def _get_skip_response(self) -> RTMReviewState:
        return {"test_suite": None}


class SynthesizerNode(StandardLLMNode):
    """MoA-inspired node that synthesizes coverage evaluations into a holistic assessment."""

    def _validate_state(self, state: RTMReviewState) -> bool:
        coverage_analysis = state.get("coverage_analysis")
        return all([
            state.get("requirement") is not None,
            state.get("decomposed_requirement") is not None,
            state.get("test_suite") is not None,
            coverage_analysis is not None and len(coverage_analysis) > 0,
        ])

    def _build_payload(self, state: RTMReviewState) -> dict:
        requirement = state.get("requirement")
        decomposed_requirement = state.get("decomposed_requirement")
        test_suite = state.get("test_suite")
        coverage_analysis = state.get("coverage_analysis")
        assert requirement is not None
        assert decomposed_requirement is not None
        assert test_suite is not None
        assert coverage_analysis is not None
        return {
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
        }

    def _format_response(self, parsed_result: Optional[SynthesizedAssessment]) -> RTMReviewState:
        return {"synthesized_assessment": parsed_result}

    def _get_skip_response(self) -> RTMReviewState:
        return {"synthesized_assessment": None}


# Factory functions

def make_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "summarizer-v2.jinja2",
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
    if "v4" in prompt_template or "v5" in prompt_template or "v6" in prompt_template:
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


def make_generator_node(client: RateLimitOpenAIClient, model: str, model_kwargs: dict, **template_vars) -> TestGeneratorNode:
    """
    Create a TestGeneratorNode with prompt loaded from Jinja2 template.

    Args:
        client: RateLimitOpenAIClient instance
        model: Model identifier string
        **template_vars: Optional variables to pass to the Jinja2 template

    Returns:
        TestGeneratorNode: Configured test generator node
    """
    system_prompt = render_prompt('test_generator.jinja2', **template_vars)
    return TestGeneratorNode(
        client=client,
        model=model,
        response_model=TestSuite,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs
    )


def make_coverage_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "coverage_evaluator-v6.jinja2",
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
    prompt_template: str = "synthesizer-v6.jinja2",
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
