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

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.agents.shared.data_integration import (
    DataIntegrationNode,
    make_transform_node_test_case_review,
    PyJamaNodeConfig,
)
from qaai.core.cache import ReviewCacheManager
from qaai.core.config import PromptConfig, settings
from qaai.utils import render_graph_png, write_graph_png_bytes

from .core import TCReviewState
from qaai.agents.shared.gate import make_validation_gate, make_gate_router
from .nodes import (
    dispatch_coverage,
    dispatch_coverage_by_requirement,
    make_aggregator_node,
    make_coverage_single_node,
    make_logical_single_node,
    make_prereqs_single_node,
    make_tc_decomposer_node,
    validate_tc_inputs,
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
        prompt_config: Optional[PromptConfig] = None,
        include_decomposition: bool = True,
    ):
        self.client = client
        self.model = model
        self.model_kwargs = model_kwargs
        self.checkpointer = checkpointer
        self.pyjama_config = pyjama_config
        # Optional shared cache; per-run behaviour driven by state["cache_mode"].
        self.cache_manager = cache_manager
        # Prompt templates resolved per node; defaults to the module-level config.
        self.prompt_config = prompt_config or settings.prompt_config
        # When False, skip requirement decomposition entirely and review the test
        # case directly against the original requirement text.
        self.include_decomposition = include_decomposition
        self.graph = self.build()

    def build(self) -> Runnable:
        sg = StateGraph(TCReviewState)
        pc = self.prompt_config

        # Data integration layer
        data_integration = DataIntegrationNode(self.pyjama_config)
        transform = make_transform_node_test_case_review()

        cm = self.cache_manager
        coverage_eval = make_coverage_single_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=pc.single_test_coverage_eval, cache_manager=cm,
            include_decomposition=self.include_decomposition,
        )
        logical_eval = make_logical_single_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=pc.single_test_logical_steps, cache_manager=cm,
        )
        prereqs_eval = make_prereqs_single_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=pc.single_test_prereqs, cache_manager=cm,
        )
        aggregator = make_aggregator_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=pc.single_test_aggregator, cache_manager=cm,
        )

        # Add the shared nodes
        sg.add_node("data_integration", data_integration)
        sg.add_node("transform", transform)
        sg.add_node("validation_gate", make_validation_gate(validate_tc_inputs))
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
        sg.add_edge("transform", "validation_gate")

        # Input gate: skip the graph (no LLM calls) when the test case has no
        # traced requirements or no step text. The first node after the gate
        # differs by mode: the decomposer (with decomposition) or the
        # coverage_router directly (no decomposition).
        if self.include_decomposition:
            decomposer = make_tc_decomposer_node(
                self.client, self.model, self.model_kwargs,
                prompt_template=pc.decomposer, cache_manager=cm,
            )
            sg.add_node("decomposer", decomposer)
            sg.add_conditional_edges(
                "validation_gate",
                make_gate_router(["decomposer"]),
                ["decomposer", END],
            )
            sg.add_edge("decomposer", "coverage_router")
            # Coverage axis fans out per spec via Send.
            coverage_dispatch = dispatch_coverage
        else:
            sg.add_conditional_edges(
                "validation_gate",
                make_gate_router(["coverage_router"]),
                ["coverage_router", END],
            )
            # Coverage axis fans out per requirement via Send (no specs).
            coverage_dispatch = dispatch_coverage_by_requirement

        # Coverage fans out via Send. Logical and prereqs are test-case-level
        # (single LLM call each) — direct edges, no Send.
        sg.add_conditional_edges("coverage_router", coverage_dispatch, ["coverage_evaluator"])
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
