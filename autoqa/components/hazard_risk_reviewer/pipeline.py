"""LangGraph pipeline for the hazard risk reviewer.

Per-dimension graph with binary Yes/No verdicts (H1-H7):

    START
      ├──→ h1_evaluator ────────────────────────┐
      ├──→ h2_evaluator ────────────────────────┤
      ├──→ h3_evaluator ──────────┐             │
      ├──→ h7_evaluator ──────────┼─────────────┤
      └──→ dispatch_requirement_reviews         │
              ↓                   │             │
          requirement_reviewer × N│             │
              ↓                   │             │
          ┌───┴────┐              │             │
          h4       h5             │             │
          └───┬────┘              │             │
              └──→ h6 ────────────┘             │
                   ↓                            │
              final_assessment ←────────────────┘
                   ↓
                  END

Key improvements:
- H1, H2, H3, H7 run immediately (parallel with requirement_reviewer)
- H4, H5 run after requirement_reviews complete
- H6 runs after H3, H4, H5 complete (validates residual risk against upstream evidence)
- Final assessor waits for all 7 findings

overall_verdict is computed deterministically: Yes iff every
mandatory_findings[i].verdict ∈ {Yes, N-A} (only H5 may be N-A).
"""

from pathlib import Path
from typing import Optional, Union

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.config import PromptConfig, settings
from autoqa.utils import save_graph_png

from .core import HazardReviewState
from .nodes import (
    dispatch_hazard_evaluators_early,
    dispatch_hazard_evaluators_late,
    dispatch_requirement_reviews,
    make_final_assessor_node,
    make_h1_evaluator_node,
    make_h2_evaluator_node,
    make_h3_evaluator_node,
    make_h4_evaluator_node,
    make_h5_evaluator_node,
    make_h6_evaluator_node,
    make_h7_evaluator_node,
    make_requirement_reviewer_node,
)


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
    ):
        self.client = client
        self.model = model
        self.model_kwargs = model_kwargs
        self.checkpointer = checkpointer
        self.prompt_config = prompt_config if prompt_config is not None else settings.prompt_config
        # The RTM subgraph is built once and reused across all Send fan-outs.
        # Callers can inject a pre-built RTMReviewerRunnable to share a
        # single compiled graph between this service and an RTMReviewService.
        self.rtm = rtm_runnable or RTMReviewerRunnable(
            client=client,
            model=model,
            model_kwargs=model_kwargs,
            prompt_config=self.prompt_config,
        )
        self.graph = self.build()

    def build(self) -> Runnable:
        sg = StateGraph(HazardReviewState)

        # Create all 7 evaluator nodes
        h1 = make_h1_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h1,
        )
        h2 = make_h2_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h2,
        )
        h3 = make_h3_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h3,
        )
        h4 = make_h4_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h4,
        )
        h5 = make_h5_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h5,
        )
        h6 = make_h6_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h6,
        )
        h7 = make_h7_evaluator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_h7,
        )
        final_assessor = make_final_assessor_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_final,
        )
        requirement_reviewer = make_requirement_reviewer_node(self.rtm)

        # Add all nodes to the graph
        sg.add_node("h1_evaluator", h1)
        sg.add_node("h2_evaluator", h2)
        sg.add_node("h3_evaluator", h3)
        sg.add_node("h4_evaluator", h4)
        sg.add_node("h5_evaluator", h5)
        sg.add_node("h6_evaluator", h6)
        sg.add_node("h7_evaluator", h7)
        sg.add_node("requirement_reviewer", requirement_reviewer)
        sg.add_node("final_assessment", final_assessor)

        # Early evaluators (H1, H2, H3, H7) run immediately from START
        sg.add_conditional_edges(
            START,
            dispatch_hazard_evaluators_early,
            ["h1_evaluator", "h2_evaluator", "h3_evaluator", "h7_evaluator"],
        )

        # Requirement reviews also start from START (parallel with early evaluators)
        sg.add_conditional_edges(START, dispatch_requirement_reviews, ["requirement_reviewer"])

        # Late evaluators (H4, H5) wait for requirement_reviews
        sg.add_conditional_edges(
            "requirement_reviewer",
            dispatch_hazard_evaluators_late,
            ["h4_evaluator", "h5_evaluator"],
        )

        # H6 waits for H3, H4, H5 (3-way join)
        sg.add_edge("h3_evaluator", "h6_evaluator")
        sg.add_edge("h4_evaluator", "h6_evaluator")
        sg.add_edge("h5_evaluator", "h6_evaluator")

        # Final assessment waits for all 7 evaluators
        sg.add_edge("h1_evaluator", "final_assessment")
        sg.add_edge("h2_evaluator", "final_assessment")
        sg.add_edge("h6_evaluator", "final_assessment")  # H6 already waited for H3, H4, H5
        sg.add_edge("h7_evaluator", "final_assessment")

        sg.add_edge("final_assessment", END)

        flow = sg.compile(checkpointer=self.checkpointer)
        save_graph_png(flow, Path(settings.log_file_path).parent / "hazard_graph.png")
        return flow
