"""
LangGraph pipeline for the hazard risk reviewer.

Per-dimension graph with binary Yes/No verdicts:

    START
      ├──→ h1_evaluator                  (Hazard Statement Completeness)
      ├──→ h2_evaluator                  (Pre-Mitigation Risk)
      └──→ dispatch_requirement_reviews  (Send × N)
              ↓
          requirement_reviewer × N (parallel — each invokes the shared
                                    compiled RTMReviewerRunnable.graph)
              ↓ fan-in: operator.add on requirement_reviews
          ┌────────────┐         ┌────────────┐
          │ h3_evaluator│         │ h4_evaluator│
          │  (Risk      │         │ (Verification│
          │   Control)  │         │     Depth)  │
          └─────┬──────┘         └─────┬──────┘
                └────────┬───────────────┘
                         ↓ (4-way join on H1, H2, H3, H4)
                   h5_evaluator           (Residual Risk Closure)
                         ↓
                  final_assessment        (deterministic verdict +
                                           LLM-written prose)
                         ↓
                        END

overall_verdict is computed deterministically: Yes iff every
mandatory_findings[i].verdict ∈ {Yes, N-A} (only H4 may be N-A).
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
    dispatch_requirement_reviews,
    make_final_assessor_node,
    make_h1_evaluator_node,
    make_h2_evaluator_node,
    make_h3_evaluator_node,
    make_h4_evaluator_node,
    make_h5_evaluator_node,
    make_requirement_reviewer_node,
)


class HazardReviewerRunnable:
    """
    LangGraph-based hazard reviewer. Evaluates whether a HazardRecord's
    traced requirements + test cases provide reasonable assurance of safety
    against the hazard, applying the H1-H5 rubric defined by the
    review-hazard-mitigation-coverage skill.

    Graph runs five per-dimension LLM evaluators (one per H1..H5) plus a
    deterministic final_assessor. H1 and H2 evaluate hazard fields in
    isolation and run from START in parallel with the requirement-review
    fan-out. H3 and H4 fire after every Send-fanned requirement_reviewer
    completes — they evaluate the *list* of per-requirement
    SynthesizedAssessment outputs at the requirement level (not spec-by-
    spec). H5 joins on H1-H4 for residual-risk closure. Each H1..H5
    finding is binary Yes/No (H4 may also be N-A). overall_verdict is
    computed in code as Yes iff every dimension's verdict is in {Yes, N-A}.
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
        final_assessor = make_final_assessor_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.hazard_final,
        )
        requirement_reviewer = make_requirement_reviewer_node(self.rtm)

        sg.add_node("h1_evaluator", h1)
        sg.add_node("h2_evaluator", h2)
        sg.add_node("requirement_reviewer", requirement_reviewer)
        sg.add_node("h3_evaluator", h3)
        sg.add_node("h4_evaluator", h4)
        sg.add_node("h5_evaluator", h5)
        sg.add_node("final_assessment", final_assessor)

        # Three parallel paths from START: H1, H2, and Send fan-out to N requirement_reviewers.
        sg.add_edge(START, "h1_evaluator")
        sg.add_edge(START, "h2_evaluator")
        sg.add_conditional_edges(START, dispatch_requirement_reviews, ["requirement_reviewer"])

        # After requirement_reviewer fan-in, H3 and H4 evaluate the requirement_reviews list.
        sg.add_edge("requirement_reviewer", "h3_evaluator")
        sg.add_edge("requirement_reviewer", "h4_evaluator")

        # H5 joins on H1, H2, H3, H4.
        sg.add_edge("h1_evaluator", "h5_evaluator")
        sg.add_edge("h2_evaluator", "h5_evaluator")
        sg.add_edge("h3_evaluator", "h5_evaluator")
        sg.add_edge("h4_evaluator", "h5_evaluator")

        # Final deterministic assembly + LLM-written prose.
        sg.add_edge("h5_evaluator", "final_assessment")
        sg.add_edge("final_assessment", END)

        flow = sg.compile(checkpointer=self.checkpointer)
        save_graph_png(flow, Path(settings.log_file_path).parent / "hazard_graph.png")
        return flow
