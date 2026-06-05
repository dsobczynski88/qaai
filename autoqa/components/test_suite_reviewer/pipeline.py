import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from autoqa.core.config import settings, PromptConfig
from autoqa.core.cache import ReviewCacheManager
from autoqa.utils import save_graph_png
from autoqa.prj_logger import ProjectLogger
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.shared.data_integration import (
    DataIntegrationNode,
    make_transform_node_test_suite_review,
    PyJamaNodeConfig,
)
from .core import RTMReviewState
from .nodes import (
    make_coverage_evaluator,
    make_decomposer_node,
    make_summarizer_node,
    make_design_summarizer_node,
    make_synthesizer_node,
    dispatch_coverage,
)


class RTMReviewerRunnable:
    """
    LangGraph-based RTM reviewer that evaluates how well a supplied test suite
    covers a single requirement.

    A Requirement plus its traced test cases enters at START. Decomposer and
    Summarizer run in parallel: the decomposer splits the requirement into
    atomic specs; the summarizer condenses each raw test case into an objective/
    protocol/acceptance-criteria summary. Both outputs are needed before coverage
    evaluation, so coverage_router serves as the join barrier.

    After the join, dispatch_coverage fans out one Send per decomposed spec to
    the spec_evaluator node — so spec_evaluator runs N times in parallel, each
    call scoring coverage of one spec against the summarized test suite. The
    operator.add reducer on coverage_analysis accumulates these per-spec verdicts.

    Finally, the synthesizer performs MoA-style aggregation across all per-spec
    verdicts to produce a single holistic SynthesizedAssessment.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        checkpointer: Union[MemorySaver, None] = None,
        prompt_config: Optional[PromptConfig] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):

        self.client = client
        self.model = model
        self.model_kwargs = model_kwargs
        self.checkpointer = checkpointer # currently the graph collects intermediate responses via operator.add (no specific checkpointer implemented)
        self.prompt_config = prompt_config if prompt_config is not None else settings.prompt_config
        self.pyjama_config = pyjama_config
        # Optional shared cache. Per-run behaviour is driven by state["cache_mode"];
        # when None (e.g. the embedded RTM subgraph inside the hazard reviewer) no
        # node caches. See autoqa.core.cache.ReviewCacheManager.
        self.cache_manager = cache_manager
        self.graph = self.build()


    def build(self) -> Runnable:
        """
        Build the graph to evaluate test case suites.

        Graph structure:
            START
              ↓
            ┌─────────────────────────────────┐
            │DATA_INTEGRATION (fetch/passthru)│
            └─────────────────────────────────┘
              ↓
            ┌─────────────────────────────────┐
            │TRANSFORM (JAMA→state or no-op)  │
            └─────────────────────────────────┘
              ↓
            ┌─────────────────────────────────┐
            │DECOMPOSER, SUMMARIZER (parallel)│
            └─────────────────────────────────┘
              ↓ (fan-in: waits for both)
            ┌─────────────────────────────────┐
            │COVERAGE_ROUTER (sync point)     │
            └─────────────────────────────────┘
              ↓ dispatch_coverage → Send × N
            ┌─────────────────────────────────────────────────┐
            │SPEC_EVALUATOR × N  (parallel)                   │
            └─────────────────────────────────────────────────┘
              ↓ (fan-in: operator.add on coverage_analysis)
            ┌─────────────────────────────────────────────────┐
            │SYNTHESIZER  MoA-like aggregation                │
            └─────────────────────────────────────────────────┘
              ↓
            END
        """
        sg = StateGraph(RTMReviewState)

        # Data integration layer
        data_integration = DataIntegrationNode(self.pyjama_config)
        transform = make_transform_node_test_suite_review()

        cm = self.cache_manager
        decomposer = make_decomposer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.decomposer,
            cache_manager=cm,
        )
        summarizer = make_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.summarizer,
            cache_manager=cm,
        )
        design_summarizer = make_design_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.design_summarizer,
            cache_manager=cm,
        )
        spec_evaluator = make_coverage_evaluator(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.coverage,
            cache_manager=cm,
        )
        synthesizer = make_synthesizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.synthesizer,
            cache_manager=cm,
        )

        # Add all nodes
        sg.add_node("data_integration", data_integration)
        sg.add_node("transform", transform)
        sg.add_node("decomposer", decomposer)
        sg.add_node("summarizer", summarizer)
        sg.add_node("design_summarizer", design_summarizer)
        # Join barrier: LangGraph's add_conditional_edges needs a single named source,
        # so we land decomposer + summarizer + design_summarizer here before dispatch_coverage fans out.
        sg.add_node("coverage_router", lambda state: {})
        sg.add_node("spec_evaluator", spec_evaluator)
        sg.add_node("synthesizer", synthesizer)

        # Wire data integration layer
        sg.add_edge(START, "data_integration")
        sg.add_edge("data_integration", "transform")

        # Decomposer, summarizer, and design_summarizer run in parallel from transform
        sg.add_edge("transform", "decomposer")
        sg.add_edge("transform", "summarizer")
        sg.add_edge("transform", "design_summarizer")

        # Fan-in to coverage_router, then fan-out via Send to N parallel spec evaluators
        sg.add_edge("decomposer", "coverage_router")
        sg.add_edge("summarizer", "coverage_router")
        sg.add_edge("design_summarizer", "coverage_router")
        sg.add_conditional_edges("coverage_router", dispatch_coverage, ["spec_evaluator"])

        # Synthesizer aggregates coverage evaluations (MoA-like pattern)
        # operator.add on coverage_analysis acts as fan-in across all spec_evaluator results
        sg.add_edge("spec_evaluator", "synthesizer")
        sg.add_edge("synthesizer", END)

        flow = sg.compile(checkpointer=self.checkpointer)
        save_graph_png(flow, Path(settings.log_file_path).parent / "graph.png")
        return flow