"""Single-test-case reviewer component."""

from .core import (
    DecomposedRequirement,
    DecomposedSpec,
    EvaluatedReviewObjective,
    OverallAnalysis,
    Requirement,
    ReviewObjective,
    SpecAnalysis,
    TCReviewState,
    TestCase,
    TestCaseAssessment,
    Verdict,
)
from .nodes import (
    AggregatorNode,
    OverallLogicalNode,
    OverallPrereqsNode,
    SingleSpecCoverageNode,
    TCDecomposerNode,
    dispatch_coverage,
    load_default_review_objectives,
    make_aggregator_node,
    make_coverage_single_node,
    make_logical_single_node,
    make_prereqs_single_node,
    make_tc_decomposer_node,
)
from .pipeline import TCReviewerRunnable

__all__ = [
    # core models
    "Requirement",
    "DecomposedSpec",
    "DecomposedRequirement",
    "TestCase",
    "Verdict",
    "ReviewObjective",
    "EvaluatedReviewObjective",
    "SpecAnalysis",
    "OverallAnalysis",
    "TestCaseAssessment",
    "TCReviewState",
    # nodes
    "TCDecomposerNode",
    "SingleSpecCoverageNode",
    "OverallLogicalNode",
    "OverallPrereqsNode",
    "AggregatorNode",
    # factories
    "make_tc_decomposer_node",
    "make_coverage_single_node",
    "make_logical_single_node",
    "make_prereqs_single_node",
    "make_aggregator_node",
    # dispatcher (only coverage fans out per spec from v3 onwards)
    "dispatch_coverage",
    # helpers
    "load_default_review_objectives",
    # runnable
    "TCReviewerRunnable",
]
