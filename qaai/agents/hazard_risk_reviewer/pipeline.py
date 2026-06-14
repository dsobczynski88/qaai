"""LangGraph pipeline for the hazard risk reviewer.

Per-dimension graph with binary Yes/No verdicts (H1-H7):

    START
      ├──→ h1_evaluator ────────────────────────┐
      ├──→ h2_evaluator ────────────────────────┤
      ├──→ h3_evaluator ────────────────────────┤  (finding accumulated via reducer)
      ├──→ h7_evaluator ────────────────────────┤
      └──→ dispatch_requirement_reviews         │
              ↓                                 │
          requirement_reviewer × N              │
              ↓                                 │
          ┌───┴────┐                            │
          h4       h5                           │
          └───┬────┘                            │
              └──→ h6 ──────────────────────────┤
                   ↓                            │
              final_assessment ←────────────────┘
                   ↓
                  END

Key improvements:
- H1, H2, H3, H7 run immediately (parallel with requirement_reviewer)
- H4, H5 run after requirement_reviews complete
- H6 runs after H4 and H5 complete (2-way fan-in, same superstep); H3's finding
  is already in the hazard_findings reducer by that point — no direct H3→H6 edge
  is needed and adding one would cause H6 to fire prematurely (before H4/H5 exist)
- Final assessor waits for all 7 findings

overall_verdict is computed deterministically: Yes iff every
mandatory_findings[i].verdict ∈ {Yes, N-A} (only H5 may be N-A).
"""

import logging
from pathlib import Path
from typing import Any, Optional, Union

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.agents.shared.data_integration import (
    DataIntegrationNode,
    PyJamaNodeConfig,
    make_transform_node_bidirectional_trace,
)
from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.core.cache import ReviewCacheManager
from qaai.core.config import PromptConfig, settings
from qaai.utils import render_graph_png, write_graph_png_bytes

from .core import HazardReviewState
from .nodes import (
    dispatch_hazard_evaluators_early,
    dispatch_hazard_evaluators_late,
    dispatch_requirement_reviews,
    make_final_assessor_node,
    make_hazard_evaluator_node,
    make_h6_evaluator_node,
    make_hazard_design_summarizer_node,
    make_hazard_needs_summarizer_node,
    RequirementReviewerNode,
)

logger = logging.getLogger(__name__)


class HazardReviewerRunnable:
    """
    LangGraph-based hazard reviewer. Evaluates whether a HazardRecord's
    traced requirements + test cases provide reasonable assurance of safety
    against the hazard, applying the H1-H7 rubric defined by the
    review-hazard-mitigation-coverage skill.

    Graph runs seven per-dimension LLM evaluators (one per H1..H7) plus a
    deterministic final_assessor. H1, H2, H3, H7 evaluate hazard fields in
    isolation and run from START in parallel with the requirement-review
    fan-out. H4 and H5 fire after every Send-fanned requirement_reviewer
    completes — they evaluate the *list* of per-requirement
    SynthesizedAssessment outputs at the requirement level (not spec-by-
    spec). H6 joins on H3, H4, H5 for residual-risk closure validation.
    Each H1..H7 finding is binary Yes/No (H5 may also be N-A). overall_verdict
    is computed in code as Yes iff every dimension's verdict is in {Yes, N-A}.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        checkpointer: Union[MemorySaver, None] = None,
        prompt_config: Optional[PromptConfig] = None,
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
        cache_manager: Optional[ReviewCacheManager] = None,
        telemetry_tracker: Optional[Any] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
    ):
        self.client = client
        self.model = model
        self.model_kwargs = model_kwargs
        self.checkpointer = checkpointer
        self.prompt_config = prompt_config if prompt_config is not None else settings.prompt_config
        self.pyjama_config = pyjama_config
        # The RTM subgraph is built once and reused across all Send fan-outs.
        # Callers can inject a pre-built RTMReviewerRunnable to share a
        # single compiled graph between this service and an RTMReviewService.
        self.rtm = rtm_runnable or RTMReviewerRunnable(
            client=client,
            model=model,
            model_kwargs=model_kwargs,
            prompt_config=self.prompt_config,
        )

        # Build cache manager if not injected and caching is enabled.
        # Callers can pass a pre-wired ReviewCacheManager (e.g. to share
        # between this reviewer and the RTMReviewService), or let the
        # pipeline auto-build one from settings.
        if cache_manager is not None:
            self.cache_manager: Optional[ReviewCacheManager] = cache_manager
        elif settings.enable_cache:
            # Reuse the tracker already wired into the client so cache events
            # land in the same JSONL file as normal LLM-call records.
            # A brand-new TokenUsageTracker would clear the file on init.
            resolved_tracker = telemetry_tracker or getattr(client, "telemetry_tracker", None)
            self.cache_manager = ReviewCacheManager(
                cache_dir=settings.cache_dir,
                redis_url=settings.redis_url,
                telemetry_tracker=resolved_tracker,
            )
        else:
            self.cache_manager = None

        self.graph = self.build()

    def build(self) -> Runnable:
        sg = StateGraph(HazardReviewState)

        # Create data integration node (entry point for conditional JAMA fetch)
        # followed by the transform node that merges a bidirectional_trace JAMA
        # response onto the hazard's requirements_traceability. In Excel/local
        # mode both are no-ops and the in-state hazard flows through unchanged.
        data_integration = DataIntegrationNode(self.pyjama_config)
        transform = make_transform_node_bidirectional_trace()

        cm = self.cache_manager
        ps = self.prompt_config.set_name

        # Create all 7 evaluator nodes
        h1 = make_hazard_evaluator_node(
            "H1", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h1,
            cache_manager=cm, prompt_set=ps,
        )
        h2 = make_hazard_evaluator_node(
            "H2", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h2,
            cache_manager=cm, prompt_set=ps,
        )
        h3 = make_hazard_evaluator_node(
            "H3", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h3,
            cache_manager=cm, prompt_set=ps,
        )
        h4 = make_hazard_evaluator_node(
            "H4", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h4,
            cache_manager=cm, prompt_set=ps,
        )
        h5 = make_hazard_evaluator_node(
            "H5", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h5,
            cache_manager=cm, prompt_set=ps,
        )
        h6 = make_h6_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h6,
            cache_manager=cm, prompt_set=ps,
        )
        h7 = make_hazard_evaluator_node(
            "H7", self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h7,
            cache_manager=cm, prompt_set=ps,
        )
        final_assessor = make_final_assessor_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_final,
            cache_manager=cm, prompt_set=ps,
        )
        requirement_reviewer = RequirementReviewerNode(
            self.rtm,
            cache_manager=cm,
            rtm_prompt_version=ReviewCacheManager.extract_prompt_version(
                self.prompt_config.synthesizer
            ),
            prompt_set=ps,
        )

        # Create summarizer nodes
        design_summarizer = make_hazard_design_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_design_summarizer,
            cache_manager=cm, prompt_set=ps,
        )
        needs_summarizer = make_hazard_needs_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_needs_summarizer,
            cache_manager=cm, prompt_set=ps,
        )

        # Add all nodes to the graph
        sg.add_node("data_integration", data_integration)
        sg.add_node("transform", transform)
        sg.add_node("h1_evaluator", h1)
        sg.add_node("h2_evaluator", h2)
        sg.add_node("h3_evaluator", h3)
        sg.add_node("h4_evaluator", h4)
        sg.add_node("h5_evaluator", h5)
        sg.add_node("h6_evaluator", h6)
        sg.add_node("h7_evaluator", h7)
        sg.add_node("requirement_reviewer", requirement_reviewer)
        sg.add_node("design_summarizer", design_summarizer)
        sg.add_node("needs_summarizer", needs_summarizer)
        sg.add_node("final_assessment", final_assessor)

        # Data integration runs first (conditionally fetches from JAMA or passes
        # through), then transform merges any JAMA traceability onto the hazard.
        sg.add_edge(START, "data_integration")
        sg.add_edge("data_integration", "transform")

        # Early evaluators (H1, H2, H3, H7) run after the transform
        sg.add_conditional_edges(
            "transform",
            dispatch_hazard_evaluators_early,
            ["h1_evaluator", "h2_evaluator", "h3_evaluator", "h7_evaluator"],
        )

        # Requirement reviews also flow from transform
        sg.add_conditional_edges("transform", dispatch_requirement_reviews, ["requirement_reviewer"])

        # Summarizers also flow from transform
        sg.add_edge("transform", "design_summarizer")
        sg.add_edge("transform", "needs_summarizer")

        # Late evaluators (H4, H5) wait for requirement_reviews AND summarizers
        # We need a join node to synchronize requirement_reviewer + design_summarizer + needs_summarizer
        sg.add_node("late_evaluator_router", lambda state: {})
        
        # All three must complete before late_evaluator_router
        sg.add_edge("requirement_reviewer", "late_evaluator_router")
        sg.add_edge("design_summarizer", "late_evaluator_router")
        sg.add_edge("needs_summarizer", "late_evaluator_router")
        
        # Then dispatch H4, H5 from the router
        sg.add_conditional_edges(
            "late_evaluator_router",
            dispatch_hazard_evaluators_late,
            ["h4_evaluator", "h5_evaluator"],
        )

        # H6 waits for H4 and H5 (2-way join, same superstep).
        # H3 runs early and its finding reaches H6 via the hazard_findings
        # reducer — a direct h3→h6 edge would fire H6 a full superstep early,
        # before H4/H5 exist, causing a spurious "validation failed" skip.
        sg.add_edge("h4_evaluator", "h6_evaluator")
        sg.add_edge("h5_evaluator", "h6_evaluator")

        # Final assessment waits for all 7 evaluators
        sg.add_edge("h1_evaluator", "final_assessment")
        sg.add_edge("h2_evaluator", "final_assessment")
        sg.add_edge("h6_evaluator", "final_assessment")  # H6 already waited for H3, H4, H5
        sg.add_edge("h7_evaluator", "final_assessment")

        sg.add_edge("final_assessment", END)

        flow = sg.compile(checkpointer=self.checkpointer)
        # Render once (mermaid.ink); each run writes the cached bytes via write_graph_png.
        self._graph_png_bytes = render_graph_png(flow)
        return flow

    def write_graph_png(self, run_dir: Union[str, Path]) -> None:
        """Write the cached graph diagram into a per-run folder as hazard_graph.png."""
        write_graph_png_bytes(self._graph_png_bytes, Path(run_dir) / "hazard_graph.png")
