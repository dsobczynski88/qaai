import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from qaai.core.config import settings, PromptConfig
from qaai.core.cache import ReviewCacheManager
from qaai.utils import render_graph_png, write_graph_png_bytes
from qaai.agents.clients import RateLimitOpenAIClient
from qaai.agents.shared.data_integration import (
    DataIntegrationNode,
    make_transform_node_test_suite_review,
    PyJamaNodeConfig,
)
from .core import RTMReviewState
from qaai.agents.shared.gate import make_validation_gate, make_gate_router
from .nodes import (
    make_coverage_evaluator,
    make_decomposer_node,
    make_summarizer_node,
    make_design_summarizer_node,
    make_synthesizer_node,
    dispatch_coverage,
    validate_rtm_inputs,
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
        # node caches. See qaai.core.cache.ReviewCacheManager.
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
        ps = self.prompt_config.set_name
        decomposer = make_decomposer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.decomposer,
            cache_manager=cm, prompt_set=ps,
        )
        summarizer = make_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.summarizer,
            cache_manager=cm, prompt_set=ps,
        )
        design_summarizer = make_design_summarizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.design_summarizer,
            cache_manager=cm, prompt_set=ps,
        )
        spec_evaluator = make_coverage_evaluator(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.coverage,
            cache_manager=cm, prompt_set=ps,
        )
        synthesizer = make_synthesizer_node(
            self.client, self.model, self.model_kwargs,
            prompt_template=self.prompt_config.synthesizer,
            cache_manager=cm, prompt_set=ps,
        )

        # Add all nodes
        sg.add_node("data_integration", data_integration)
        sg.add_node("transform", transform)
        sg.add_node("validation_gate", make_validation_gate(validate_rtm_inputs))
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

        # Input gate: skip the graph (no LLM calls) when the requirement has no
        # text or no traced test cases; otherwise fan out to the work nodes.
        sg.add_edge("transform", "validation_gate")
        sg.add_conditional_edges(
            "validation_gate",
            make_gate_router(["decomposer", "summarizer", "design_summarizer"]),
            ["decomposer", "summarizer", "design_summarizer", END],
        )

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
        # Render the diagram once (network call to mermaid.ink); each run writes
        # the cached bytes into its own folder via write_graph_png(run_dir).
        self._graph_png_bytes = render_graph_png(flow)
        return flow

    def write_graph_png(self, run_dir: Union[str, Path]) -> None:
        """Write the cached graph diagram into a per-run folder as graph.png."""
        write_graph_png_bytes(self._graph_png_bytes, Path(run_dir) / "graph.png")