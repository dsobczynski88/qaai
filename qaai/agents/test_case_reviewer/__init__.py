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
    RequirementCoveragePipelineNode,
    SingleSpecCoverageNode,
    dispatch_requirement_pipeline,
    make_aggregator_node,
    make_coverage_single_node,
    make_logical_single_node,
    make_prereqs_single_node,
    make_requirement_coverage_pipeline_node,
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
    "RequirementCoveragePipelineNode",
    "SingleSpecCoverageNode",
    "OverallLogicalNode",
    "OverallPrereqsNode",
    "AggregatorNode",
    # factories
    "make_requirement_coverage_pipeline_node",
    "make_coverage_single_node",
    "make_logical_single_node",
    "make_prereqs_single_node",
    "make_aggregator_node",
    # dispatcher (decomposition mode fans out per requirement to requirement_pipeline)
    "dispatch_requirement_pipeline",
    # runnable
    "TCReviewerRunnable",
]
