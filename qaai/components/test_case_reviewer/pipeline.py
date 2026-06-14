"""
LangGraph pipeline for the single-test-case reviewer.

A TestCase plus its traced Requirements (and a review-objectives checklist)
enter at START. The data_integration node conditionally fetches from JAMA
(if pyjama_request present) or passes through local data. The transform node
converts JAMA data to state format. The decomposer splits each requirement
into atomic specs. A no-op coverage_router then fans out three independent
waves of Sends — one per review axis — to per-spec evaluators that run in
parallel. The aggregator synthesizes the three accumulated SpecAnalysis lists
into a single TestCaseAssessment with the review-objectives checklist populated.
"""
from pathlib import Path
from typing import Optional, Union

from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from qaai.components.clients import RateLimitOpenAIClient
from qaai.components.shared.data_integration import (
    DataIntegrationNode,
    make_transform_node_test_case_review,
    PyJamaNodeConfig,
)
from qaai.core.cache import ReviewCacheManager
from qaai.core.config import settings
from qaai.utils import render_graph_png, write_graph_png_bytes

from .core import TCReviewState
from .nodes import (
    dispatch_coverage,
    make_aggregator_node,
    make_coverage_single_node,
    make_logical_single_node,
    make_prereqs_single_node,
    make_tc_decomposer_node,
)


class TCReviewerRunnable:
    """
    LangGraph-based single-test-case reviewer.

    Graph structure:
        START
          ↓
        ┌──────────────────────────────────────────┐
        │ DATA_INTEGRATION (fetch/passthru)          │
        └──────────────────────────────────────────┘
          ↓
        ┌──────────────────────────────────────────┐
        │ TRANSFORM (JAMA→state or no-op)            │
        └──────────────────────────────────────────┘
          ↓
        ┌──────────────────────────────────────────┐
        │ DECOMPOSER (sequential loop over reqs)    │
        └──────────────────────────────────────────┘
          ↓
        ┌──────────────────────────────────────────┐
        │ COVERAGE_ROUTER (sync no-op)              │
        └──────────────────────────────────────────┘
          ↓ coverage Send × N (per spec); logical & prereqs direct edges (test-level)
        ┌─────────────┬─────────────┬─────────────┐
        │ coverage_   │ logical_    │ prereqs_    │
        │ evaluator×N │ evaluator×N │ evaluator×N │
        └─────────────┴─────────────┴─────────────┘
          ↓ (operator.add reducers fan in per axis)
        ┌──────────────────────────────────────────┐
        │ AGGREGATOR  (MoA-like synthesis)          │
        └──────────────────────────────────────────┘
          ↓
        END
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        checkpointer: Union[MemorySaver, None] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional[ReviewCacheManager] = None,
    ):
        self.client = client
        self.model = model
        self.model_kwargs = model_kwargs
        self.checkpointer = checkpointer
        self.pyjama_config = pyjama_config
        # Optional shared cache; per-run behaviour driven by state["cache_mode"].
        self.cache_manager = cache_manager
        self.graph = self.build()

    def build(self) -> Runnable:
        sg = StateGraph(TCReviewState)

        # Data integration layer
        data_integration = DataIntegrationNode(self.pyjama_config)
        transform = make_transform_node_test_case_review()

        cm = self.cache_manager
        decomposer = make_tc_decomposer_node(
            self.client, self.model, self.model_kwargs, cache_manager=cm,
        )
        coverage_eval = make_coverage_single_node(
            self.client, self.model, self.model_kwargs, cache_manager=cm,
        )
        logical_eval = make_logical_single_node(
            self.client, self.model, self.model_kwargs, cache_manager=cm,
        )
        prereqs_eval = make_prereqs_single_node(
            self.client, self.model, self.model_kwargs, cache_manager=cm,
        )
        aggregator = make_aggregator_node(
            self.client, self.model, self.model_kwargs, cache_manager=cm,
        )

        # Add all nodes
        sg.add_node("data_integration", data_integration)
        sg.add_node("transform", transform)
        sg.add_node("decomposer", decomposer)
        # Join barrier: add_conditional_edges needs a single named source for
        # each fan-out. coverage_router is the shared parent that all three
        # axis dispatchers branch from.
        sg.add_node("coverage_router", lambda state: {})
        sg.add_node("coverage_evaluator", coverage_eval)
        sg.add_node("logical_evaluator", logical_eval)
        sg.add_node("prereqs_evaluator", prereqs_eval)
        sg.add_node("aggregator", aggregator)

        # Wire data integration layer
        sg.add_edge(START, "data_integration")
        sg.add_edge("data_integration", "transform")
        sg.add_edge("transform", "decomposer")
        sg.add_edge("decomposer", "coverage_router")

        # Coverage axis fans out per spec via Send. Logical and prereqs are
        # test-case-level (single LLM call each) — direct edges, no Send.
        sg.add_conditional_edges("coverage_router", dispatch_coverage, ["coverage_evaluator"])
        sg.add_edge("coverage_router", "logical_evaluator")
        sg.add_edge("coverage_router", "prereqs_evaluator")

        sg.add_edge("coverage_evaluator", "aggregator")
        sg.add_edge("logical_evaluator", "aggregator")
        sg.add_edge("prereqs_evaluator", "aggregator")
        sg.add_edge("aggregator", END)

        flow = sg.compile(checkpointer=self.checkpointer)
        # Render once (mermaid.ink); each run writes the cached bytes via write_graph_png.
        self._graph_png_bytes = render_graph_png(flow)
        return flow

    def write_graph_png(self, run_dir: Union[str, Path]) -> None:
        """Write the cached graph diagram into a per-run folder as tc_graph.png."""
        write_graph_png_bytes(self._graph_png_bytes, Path(run_dir) / "tc_graph.png")
