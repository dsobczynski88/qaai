"""Node implementations for the hazard risk reviewer.

Per-dimension graph: H1, H2, H3, H7 evaluate independent hazard fields and run
in parallel from START with `dispatch_requirement_reviews`. After every
parallel `requirement_reviewer` Send fans in, H4 and H5 evaluate the
resulting list of per-requirement SynthesizedAssessment outputs at the
requirement level (not spec-by-spec). H6 joins on H3, H4, H5 and grades
residual risk closure. The final_assessor assembles the structured
HazardAssessment deterministically (verdicts come from upstream nodes;
overall_verdict is computed in code) and uses the LLM only to write
`comments` and `clarification_questions`.

Additionally, design_summarizer and needs_summarizer nodes run in parallel
from START to summarize design documents and user needs for optional
consumption by H4/H5 evaluators.
"""

import json
import logging
from typing import Any, List, Optional

from langgraph.types import Send

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.agents.shared.nodes import BaseLLMNode, BatchedLLMNode, StandardLLMNode
from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.core.cache import ReviewCacheManager
from qaai.utils import render_prompt

from .constants import HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS
from .core import (
    FinalAssessorProse,
    HazardAssessment,
    HazardFinding,
    HazardReviewState,
    HazardSummarizedDesignSpec,
    HazardSummarizedDesignSpecList,
    HazardSummarizedUserNeed,
    HazardSummarizedUserNeedList,
    RequirementReview,
)

logger = logging.getLogger(__name__)


def validate_hazard_inputs(state: HazardReviewState) -> List[str]:
    """Input-gate check for the hazard risk reviewer.

    A review is meaningful only when the hazard record populates every required
    SHA field (HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS) and references at
    least one risk-control requirement (traced requirements or extracted control
    IDs). Returns the labels of missing inputs; an empty list means the graph
    proceeds normally. See qaai.agents.shared.gate.
    """
    hazard = state.get("hazard")
    if hazard is None:
        return ["hazard"]

    missing: List[str] = []
    for field in HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS:
        value = getattr(hazard, field, "")
        if not (value and str(value).strip()):
            missing.append(field)

    # No traced controls: neither traced requirements nor extracted control IDs.
    trace = getattr(hazard, "requirements_traceability", None)
    has_traced_reqs = bool(getattr(trace, "requirements", None)) if trace else False
    has_control_refs = bool(getattr(hazard, "row_specific_controls_references", None))
    if not has_traced_reqs and not has_control_refs:
        missing.append("risk_control_requirements")

    return missing


# --- design and user needs summarizer nodes -------------------------------


class HazardDesignSummarizerNode(BatchedLLMNode):
    """Summarizes design documents for hazard mitigation evaluation."""

    BATCH_SIZE = 5

    def _validate_state(self, state: HazardReviewState) -> bool:
        hazard = state.get("hazard")
        if hazard is None or hazard.requirements_traceability is None:
            return False
        return bool(getattr(hazard.requirements_traceability, "design_docs", None))

    def _get_items(self, state: HazardReviewState) -> list:
        return state["hazard"].requirements_traceability.design_docs

    def _build_batch_payload(self, state: HazardReviewState, batch: List) -> dict:
        hazard = state["hazard"]
        return {
            "hazard": {
                "hazard_id": hazard.hazard_id,
                "hazardous_situation": hazard.hazardous_situation,
                "software_related_causes": hazard.software_related_causes,
                "risk_control_measures": hazard.risk_control_measures,
            },
            "design_docs": [
                {"doc_id": dd.doc_id, "name": dd.name, "description": dd.description}
                for dd in batch
            ],
        }

    def _unwrap_batch_result(self, parsed) -> list:
        return parsed.root if isinstance(parsed, HazardSummarizedDesignSpecList) else list(parsed)

    def _get_skip_response(self) -> dict:
        return {"summarized_designs": None}

    def _build_result(self, state: HazardReviewState, all_summaries: list) -> dict:
        return {"summarized_designs": all_summaries}

    def _get_cache_entity_id(self, state: HazardReviewState) -> Optional[str]:
        hazard = state.get("hazard")
        return hazard.hazard_id if hazard else None

    def _restore_from_cache(self, cached: dict) -> list:
        restored = [HazardSummarizedDesignSpec.model_validate(d) for d in cached["result"]]
        logger.info("%s: returning %d summaries from cache", self.__class__.__name__, len(restored))
        return restored


class HazardNeedsSummarizerNode(BatchedLLMNode):
    """Summarizes user needs for hazard objective evaluation."""

    BATCH_SIZE = 5

    def _validate_state(self, state: HazardReviewState) -> bool:
        hazard = state.get("hazard")
        if hazard is None or hazard.requirements_traceability is None:
            return False
        return bool(getattr(hazard.requirements_traceability, "user_needs", None))

    def _get_items(self, state: HazardReviewState) -> list:
        return state["hazard"].requirements_traceability.user_needs

    def _build_batch_payload(self, state: HazardReviewState, batch: List) -> dict:
        hazard = state["hazard"]
        return {
            "hazard": {
                "hazard_id": hazard.hazard_id,
                "hazardous_situation": hazard.hazardous_situation,
            },
            "user_needs": [
                {"req_id": un.req_id, "text": un.text}
                for un in batch
            ],
        }

    def _unwrap_batch_result(self, parsed) -> list:
        return parsed.root if isinstance(parsed, HazardSummarizedUserNeedList) else list(parsed)

    def _get_skip_response(self) -> dict:
        return {"summarized_user_needs": None}

    def _build_result(self, state: HazardReviewState, all_summaries: list) -> dict:
        return {"summarized_user_needs": all_summaries}

    def _get_cache_entity_id(self, state: HazardReviewState) -> Optional[str]:
        hazard = state.get("hazard")
        return hazard.hazard_id if hazard else None

    def _restore_from_cache(self, cached: dict) -> list:
        restored = [HazardSummarizedUserNeed.model_validate(d) for d in cached["result"]]
        logger.info("%s: returning %d summaries from cache", self.__class__.__name__, len(restored))
        return restored


# --- dispatch functions for early and late evaluators ------------------


def dispatch_hazard_evaluators_early(state: HazardReviewState) -> List[Send]:
    """
    Dispatch H1, H2, H3, H7 immediately (they only need hazard fields).
    These run in parallel with requirement_reviewer fan-out.
    """
    hazard = state.get("hazard")
    if not hazard:
        logger.warning("dispatch_hazard_evaluators_early: no hazard, skipping")
        return []
    
    # Log traceability presence for debugging
    has_traceability = hazard.requirements_traceability is not None
    num_requirements = (
        len(hazard.requirements_traceability.requirements)
        if has_traceability else 0
    )
    logger.debug(
        "dispatch_hazard_evaluators_early: hazard=%s, has_traceability=%s, num_requirements=%d",
        hazard.hazard_id, has_traceability, num_requirements
    )
    
    cache_mode = state.get("cache_mode", "partial")
    return [
        Send("h1_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
        Send("h2_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
        Send("h3_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
        Send("h7_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
    ]


def dispatch_hazard_evaluators_late(state: HazardReviewState) -> List[Send]:
    """
    Dispatch H4, H5 after requirement_reviews + summarizers complete.
    H4, H5 can handle empty requirement_reviews list gracefully.
    """
    hazard = state.get("hazard")
    if not hazard:
        logger.warning("dispatch_hazard_evaluators_late: missing hazard")
        return []
    
    # requirement_reviews defaults to empty list if not set
    requirement_reviews = state.get("requirement_reviews", [])
    summarized_designs = state.get("summarized_designs")
    summarized_user_needs = state.get("summarized_user_needs")
    
    logger.debug("dispatch_hazard_evaluators_late: dispatching H4, H5 with %d requirement_reviews", len(requirement_reviews))
    
    cache_mode = state.get("cache_mode", "partial")

    # Build state dict with optional summarized data
    h4_state = {
        "hazard": hazard,
        "requirement_reviews": requirement_reviews,
        "summarized_designs": summarized_designs,
        "cache_mode": cache_mode,
    }

    h5_state = {
        "hazard": hazard,
        "requirement_reviews": requirement_reviews,
        "summarized_user_needs": summarized_user_needs,
        "cache_mode": cache_mode,
    }

    return [
        Send("h4_evaluator", h4_state),
        Send("h5_evaluator", h5_state),
    ]


# --- dispatcher + RTM fan-out (unchanged from prior architecture) ---------


def dispatch_requirement_reviews(state: HazardReviewState) -> List[Send]:
    """
    LangGraph Send dispatcher: fans out one Send per traced requirement so
    each requirement is reviewed in parallel by RequirementReviewerNode.
    Returns an empty list when the hazard or its requirements are missing.
    """
    hazard = state.get("hazard")
    if not hazard or not hazard.requirements_traceability:
        logger.warning("dispatch_requirement_reviews: no hazard/requirements_traceability, skipping fan-out")
        return []
    
    requirements = hazard.requirements_traceability.requirements
    if not requirements:
        logger.warning("dispatch_requirement_reviews: no requirements in traceability, skipping fan-out")
        return []
    
    cache_mode = state.get("cache_mode", "partial")
    return [
        Send("requirement_reviewer", {"hazard": hazard, "requirement": req, "cache_mode": cache_mode})
        for req in requirements
    ]


class RequirementReviewerNode:
    """
    Invokes the entire compiled test_suite_reviewer graph as an atomic
    subgraph for one requirement. The wrapped RTMReviewerRunnable is shared
    across all parallel Send fan-outs — its compiled graph is built once and
    reused per requirement.

    Cache behaviour: keyed on req_id (not hazard_id). The same requirement
    often appears as a risk-control reference in multiple hazard rows — the
    cache ensures the RTM subgraph runs at most once per unique requirement
    per prompt version, both within a single run and across runs.
    """

    def __init__(
        self,
        rtm_runnable: RTMReviewerRunnable,
        cache_manager: Optional[ReviewCacheManager] = None,
        rtm_prompt_version: str = "",
        prompt_set: Optional[str] = None,
    ):
        self.rtm = rtm_runnable
        self.cache_manager = cache_manager
        self.rtm_prompt_version = rtm_prompt_version
        # Namespaces the req-blob cache by prompt set so the v3/v4 RTM subgraph
        # results for the same requirement never alias.
        self.prompt_set = prompt_set

    _NODE_NAME = "requirementreviewernode"

    async def __call__(self, state: Any) -> HazardReviewState:
        hazard = state.get("hazard")
        requirement = state.get("requirement")
        if hazard is None or requirement is None:
            logger.warning("RequirementReviewerNode: missing hazard or requirement, skipping")
            return {"requirement_reviews": []}

        req_id = requirement.req_id
        cache_mode = state.get("cache_mode", "partial")
        cache_on = cache_mode != "off"

        # --- Tier 2/3: cache check (keyed on req_id, not hazard_id) ---
        # The blob captures the WHOLE RTM subgraph result (incl. synthesizer),
        # so a hit means the test-suite review is "fully cached" for this req.
        if self.cache_manager is not None and self.rtm_prompt_version and cache_on:
            cached = await self.cache_manager.get(req_id, self._NODE_NAME, self.rtm_prompt_version, self.prompt_set)
            if cached is not None:
                try:
                    review = RequirementReview.model_validate(cached["result"])
                    logger.info(
                        "RequirementReviewerNode: cache HIT for req_id=%s — skipping RTM subgraph",
                        req_id,
                    )
                    return {"requirement_reviews": [review]}
                except Exception as e:
                    logger.warning(
                        "RequirementReviewerNode: cache restore failed for req_id=%s, re-running — %s",
                        req_id, e,
                    )

        # Test cases come from requirements_traceability
        test_cases = []
        if hazard.requirements_traceability:
            test_cases = hazard.requirements_traceability.test_cases or []

        rtm_input = {
            "requirement": requirement,
            "test_cases": test_cases,
            # Embedded RTM has no cache manager of its own (Option A), so this
            # is a no-op for its internal nodes; passed for consistency.
            "cache_mode": cache_mode,
        }

        # Pass design_docs to RTM sub-pipeline if available (from traceability)
        if (hazard.requirements_traceability
                and hasattr(hazard.requirements_traceability, 'design_docs')
                and hazard.requirements_traceability.design_docs):
            rtm_input["design_docs"] = hazard.requirements_traceability.design_docs

        # Snapshot telemetry counters so we can compute tokens consumed by this RTM call
        tracker = getattr(self.cache_manager, "telemetry_tracker", None) if self.cache_manager else None
        tokens_before_prompt = getattr(tracker, "_total_prompt_tokens", 0)
        tokens_before_completion = getattr(tracker, "_total_completion_tokens", 0)

        try:
            rtm_result = await self.rtm.graph.ainvoke(rtm_input)
        except Exception as e:
            logger.warning(
                "RequirementReviewerNode: RTM subgraph invocation failed for %s — %s",
                requirement.req_id, e,
            )
            return {
                "requirement_reviews": [
                    RequirementReview(requirement=requirement)
                ]
            }

        review = RequirementReview(
            requirement=requirement,
            synthesized_assessment=rtm_result.get("synthesized_assessment"),
            decomposed_requirement=rtm_result.get("decomposed_requirement"),
            test_suite=rtm_result.get("test_suite"),
            coverage_analysis=rtm_result.get("coverage_analysis", []),
        )

        # --- Tier 2/3: write-through cache ---
        if self.cache_manager is not None and self.rtm_prompt_version and cache_on:
            prompt_tokens = getattr(tracker, "_total_prompt_tokens", 0) - tokens_before_prompt
            completion_tokens = (
                getattr(tracker, "_total_completion_tokens", 0) - tokens_before_completion
            )
            try:
                await self.cache_manager.set(
                    entity_id=req_id,
                    node_name=self._NODE_NAME,
                    prompt_version=self.rtm_prompt_version,
                    result_dict=review.model_dump(),
                    prompt_tokens=max(0, prompt_tokens),
                    completion_tokens=max(0, completion_tokens),
                    model=self.rtm.model if hasattr(self.rtm, "model") else "",
                    prompt_set=self.prompt_set,
                )
            except Exception as e:
                logger.warning("RequirementReviewerNode: cache write failed — %s", e)

        return {"requirement_reviews": [review]}




# --- per-dimension evaluator nodes (H1-H7) -------------------------------


_H1_FIELDS = (
    "hazard_id",
    "hazard",
    "hazardous_situation",
    "hazardous_sequence_of_events",
    "software_related_causes",
    "function",
    "harm",
    "severity",
    "harm_severity_rationale",
)

_H2_FIELDS = (
    "hazard_id",
    "hazard",
    "hazardous_situation",
    "hazardous_sequence_of_events",
    "software_related_causes",
    "function",
    "ots_software",
)

_H3_FIELDS = (
    "hazard_id",
    "software_related_causes",
    "severity",
    "exploitability_pre_mitigation",
    "probability_of_harm_pre_mitigation",
    "initial_risk_rating",
    "harm_severity_rationale",
)

_H4_FIELDS = (
    "hazard_id",
    "hazardous_sequence_of_events",
    "software_related_causes",
    "risk_control_measures",
)

_H5_FIELDS = (
    "hazard_id",
    "software_related_causes",
    "risk_control_measures",
    "demonstration_of_effectiveness",
)

_H6_FIELDS = (
    "hazard_id",
    "severity",
    "probability_of_harm_pre_mitigation",
    "exploitability_pre_mitigation",
    "initial_risk_rating",
    "severity_of_harm_post_mitigation",
    "exploitability_post_mitigation",
    "probability_of_harm_post_mitigation",
    "final_risk_rating",
    "residual_risk_acceptability",
    "sw_fmea_trace",
    "sra_link",
    "urra_item",
    "new_hs_reference",
)

_H7_FIELDS = (
    "hazard_id",
    "hazard",
    "hazardous_situation",
    "hazardous_sequence_of_events",
    "software_related_causes",
    "function",
    "ots_software",
    "risk_control_measures",
    "new_hs_reference",
)

_DIMENSION_CONFIGS: dict[str, tuple] = {
    "H1": _H1_FIELDS,
    "H2": _H2_FIELDS,
    "H3": _H3_FIELDS,
    "H4": _H4_FIELDS,
    "H5": _H5_FIELDS,
    "H7": _H7_FIELDS,
}


def _slice_hazard(hazard, fields) -> dict:
    """Return the named scalar fields from a HazardRecord as a plain dict."""
    dump = hazard.model_dump()
    return {k: dump[k] for k in fields if k in dump}


def _summarise_reviews(reviews: List[RequirementReview]) -> List[dict]:
    """Compact requirement-level summary for H4/H5 prompts.

    H4/H5 evaluate at the requirement level, not spec-by-spec. The summary
    keeps the fields they actually need: requirement, overall_verdict, and
    the M1-M5 mandatory_findings list (with code, verdict, rationale, and
    optional partial flag). Decomposed specs and coverage_analysis are
    intentionally omitted to keep the LLM payload small and on-task.
    """
    out: List[dict] = []
    for r in reviews:
        sa = r.synthesized_assessment
        entry: dict = {
            "requirement": {"req_id": r.requirement.req_id, "text": r.requirement.text},
            "synthesized_assessment": None,
        }
        if sa is not None:
            entry["synthesized_assessment"] = {
                "overall_verdict": sa.overall_verdict,
                "mandatory_findings": [
                    {
                        "code": f.code,
                        "dimension": f.dimension,
                        "verdict": f.verdict,
                        "partial": getattr(f, "partial", False),
                        "rationale": f.rationale,
                        "cited_test_case_ids": f.cited_test_case_ids,
                    }
                    for f in sa.mandatory_findings
                ],
            }
        out.append(entry)
    return out


class HazardEvaluatorNode(StandardLLMNode):
    """
    Generic evaluator for a single hazard dimension (H1-H7).
    Invoked in parallel via LangGraph Send API.
    
    H1, H2, H3, H7 only need hazard fields (run early from START).
    H4, H5 need hazard + requirement_reviews (run after requirement_reviewer completes).
    H6 is special-cased in H6EvaluatorNode (needs H3, H4, H5 findings).
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        system_prompt: str,
        model_kwargs: dict,
        dimension_code: str,
        required_fields: tuple,
        cache_manager: Optional[ReviewCacheManager] = None,
        prompt_version: str = "",
        prompt_set: Optional[str] = None,
    ):
        super().__init__(
            client, model, HazardFinding, system_prompt, model_kwargs,
            cache_manager, prompt_version, prompt_set=prompt_set,
        )
        self.dimension_code = dimension_code
        self.required_fields = required_fields

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        hazard = state.get("hazard")
        return hazard.hazard_id if hazard else None

    def _get_cache_node_name(self, state: Any = None) -> str:
        # H1..H7 are all instances of this one class — disambiguate by
        # dimension so they don't share (and clobber) one cache key.
        return f"hazardevaluatornode_{self.dimension_code.lower()}"

    def _validate_state(self, state: Any) -> bool:
        return state.get("hazard") is not None

    def _build_payload(self, state: Any) -> dict:
        hazard = state["hazard"]
        payload = _slice_hazard(hazard, self.required_fields)
        
        # H4, H5 need requirement_reviews
        if self.dimension_code in ["H4", "H5"]:
            reviews = state.get("requirement_reviews", [])
            payload["requirement_reviews"] = _summarise_reviews(reviews)
            # H4, H5 also get the full requirements and test_cases lists
            payload["requirements"] = [r.requirement.model_dump() for r in reviews]
            
            # Test cases come from requirements_traceability
            test_cases = []
            if hazard.requirements_traceability:
                test_cases = hazard.requirements_traceability.test_cases or []
            payload["test_cases"] = [tc.model_dump() for tc in test_cases]
            
            # H4 gets summarized_designs if available
            if self.dimension_code == "H4":
                summarized_designs = state.get("summarized_designs")
                if summarized_designs is not None and len(summarized_designs) > 0:
                    payload["summarized_designs"] = [
                        sd.model_dump() for sd in summarized_designs
                    ]
                else:
                    payload["summarized_designs"] = None
            
            # H5 gets summarized_user_needs if available
            if self.dimension_code == "H5":
                summarized_user_needs = state.get("summarized_user_needs")
                if summarized_user_needs is not None and len(summarized_user_needs) > 0:
                    payload["summarized_user_needs"] = [
                        sun.model_dump() for sun in summarized_user_needs
                    ]
                else:
                    payload["summarized_user_needs"] = None
        
        return payload

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> dict:
        return {"hazard_findings": [parsed_result]} if parsed_result else {"hazard_findings": []}

    def _get_skip_response(self) -> dict:
        return {"hazard_findings": []}


class H6EvaluatorNode(StandardLLMNode):
    """
    H6 evaluates residual risk closure. Needs H3, H4, H5 findings
    to validate that the risk downgrade is evidence-backed.
    """

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        hazard = state.get("hazard")
        return hazard.hazard_id if hazard else None

    def _validate_state(self, state: Any) -> bool:
        findings = state.get("hazard_findings", [])
        return (
            state.get("hazard") is not None
            and all(any(f.code == code for f in findings) for code in ["H3", "H4", "H5"])
        )

    def _build_payload(self, state: Any) -> dict:
        hazard = state["hazard"]
        findings = state.get("hazard_findings", [])
        
        # Extract H3, H4, H5 findings
        h3 = next((f for f in findings if f.code == "H3"), None)
        h4 = next((f for f in findings if f.code == "H4"), None)
        h5 = next((f for f in findings if f.code == "H5"), None)
        
        payload = _slice_hazard(hazard, _H6_FIELDS)
        payload["h3_finding"] = h3.model_dump() if h3 else None
        payload["h4_finding"] = h4.model_dump() if h4 else None
        payload["h5_finding"] = h5.model_dump() if h5 else None
        
        return payload

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> dict:
        return {"hazard_findings": [parsed_result]} if parsed_result else {"hazard_findings": []}

    def _get_skip_response(self) -> dict:
        return {"hazard_findings": []}


# --- final assessor (deterministic verdict + LLM-written prose) ----------


class _FinalAssessorNode(StandardLLMNode):
    """Assembles HazardAssessment from the seven upstream HazardFindings.

    The LLM only contributes `comments` and `clarification_questions`; the
    `mandatory_findings` list and `overall_verdict` are computed in code so
    the LLM cannot accidentally re-grade or drop a dimension.
    """

    _CODES = ("H1", "H2", "H3", "H4", "H5", "H6", "H7")

    def _validate_state(self, state: HazardReviewState) -> bool:
        findings = state.get("hazard_findings", [])
        return (
            state.get("hazard") is not None
            and len(findings) == 7
            and all(any(f.code == code for f in findings) for code in self._CODES)
        )

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        findings = state.get("hazard_findings", [])
        assert hazard is not None
        
        # Sort findings by code to ensure H1-H7 order
        findings_dict = {f.code: f for f in findings}
        
        return {
            "hazard_id": hazard.hazard_id,
            **{f"h{i}_finding": findings_dict[f"H{i}"].model_dump() for i in range(1, 8)},
        }

    def _format_response(self, parsed_result: Optional[FinalAssessorProse]) -> HazardReviewState:
        # Fallback to empty prose when the LLM call returned None — the
        # deterministic verdict aggregation still proceeds.
        prose = parsed_result or FinalAssessorProse()
        return {"hazard_assessment": self._latest_assessment(prose)}

    def _get_skip_response(self) -> HazardReviewState:
        # Validation failed (one of H1-H7 missing). Return None so callers
        # can detect that the pipeline did not produce a final assessment.
        return {"hazard_assessment": None}

    # ---- deterministic-verdict helpers ----

    def _latest_assessment(self, prose: FinalAssessorProse) -> HazardAssessment:
        # _latest_state is set by __call__ before _format_response runs.
        state = self._latest_state
        hazard = state.get("hazard")
        findings = state.get("hazard_findings", [])
        assert hazard is not None
        
        # Sort findings by code
        findings_sorted = sorted(findings, key=lambda f: f.code)
        
        return HazardAssessment(
            hazard_id=hazard.hazard_id,
            mandatory_findings=findings_sorted,
            overall_verdict=self._aggregate_verdict(findings_sorted),
            comments=prose.comments,
            clarification_questions=prose.clarification_questions,
        )

    @staticmethod
    def _aggregate_verdict(findings: List[HazardFinding]) -> str:
        """Yes iff every finding's verdict is in {Yes, N-A}; else No."""
        return "Yes" if all(f.verdict in ("Yes", "N-A") for f in findings) else "No"

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        hazard = state.get("hazard")
        return hazard.hazard_id if hazard else None

    async def __call__(self, state: Any) -> Any:
        # Custom flow: when the upstream H1-H7 findings are all present we
        # always produce a HazardAssessment, even if the LLM prose call
        # fails or returns unparseable JSON (deterministic verdict
        # aggregation does not depend on the LLM). The base StandardLLMNode
        # would short-circuit to _get_skip_response on parse failure, which
        # would silently drop the assessment.
        self._latest_state = state
        if not self._validate_state(state):
            return self._get_skip_response()

        node_name = self.__class__.__name__.lower()
        entity_id = self._get_cache_entity_id(state)

        # --- Tier 2/3: cache check ---
        # This is the graph's final output node: under "partial" caching
        # _cache_read_allowed returns False so it always re-runs (fresh prose),
        # while still writing through so a later "full" run can reuse it.
        if self._cache_read_allowed(state) and entity_id:
            cached = await self.cache_manager.get(entity_id, node_name, self.prompt_version, self.prompt_set)
            if cached is not None:
                try:
                    prose = FinalAssessorProse.model_validate(cached["result"])
                    logger.info("%s: returning prose from cache", self.__class__.__name__)
                    return self._format_response(prose)
                except Exception as e:
                    logger.warning("%s: cache restore failed, re-running — %s", self.__class__.__name__, e)

        try:
            payload = self._build_payload(state)
        except Exception as e:
            logger.warning("%s: payload building failed — %s", self.__class__.__name__, e)
            return self._format_response(None)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(payload)},
        ]
        try:
            result = await self.client.chat_completion(
                model=self.model, messages=messages, **self.model_kwargs,
            )
            parsed = self._parse_llm_response(result, self.response_model, self.__class__.__name__)
        except Exception as e:
            logger.warning("%s: LLM call failed — %s", self.__class__.__name__, e)
            parsed = None

        # --- Tier 2/3: write-through cache (only when LLM succeeded) ---
        if parsed is not None and self._cache_write_allowed(state) and entity_id:
            usage = getattr(result, "usage", None)
            try:
                await self.cache_manager.set(
                    entity_id=entity_id,
                    node_name=node_name,
                    prompt_version=self.prompt_version,
                    result_dict=parsed.model_dump(),
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    model=self.model,
                    prompt_set=self.prompt_set,
                )
            except Exception as e:
                logger.warning("%s: cache write failed — %s", self.__class__.__name__, e)

        return self._format_response(parsed)


# --- factories ------------------------------------------------------------


def _make_hazard_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    dimension_code: str,
    required_fields: tuple,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> HazardEvaluatorNode:
    """Factory for generic HazardEvaluatorNode (H1-H5, H7)."""
    system_prompt = render_prompt(prompt_template, **template_vars)
    return HazardEvaluatorNode(
        client=client,
        model=model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        dimension_code=dimension_code,
        required_fields=required_fields,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


def _make_h6_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> H6EvaluatorNode:
    """Factory for specialized H6EvaluatorNode."""
    system_prompt = render_prompt(prompt_template, **template_vars)
    return H6EvaluatorNode(
        client=client,
        model=model,
        response_model=HazardFinding,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


def make_hazard_evaluator_node(
    dimension_code: str,
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> HazardEvaluatorNode:
    """Create a HazardEvaluatorNode for the given dimension (H1-H5, H7)."""
    fields = _DIMENSION_CONFIGS[dimension_code]
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code, fields, cache_manager, prompt_set=prompt_set, **template_vars,
    )


def make_h6_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> H6EvaluatorNode:
    return _make_h6_evaluator(
        client, model, model_kwargs, prompt_template,
        cache_manager=cache_manager, prompt_set=prompt_set,
        **template_vars,
    )


def make_final_assessor_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> _FinalAssessorNode:
    system_prompt = render_prompt(prompt_template, **template_vars)
    return _FinalAssessorNode(
        client=client,
        model=model,
        response_model=FinalAssessorProse,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        is_final_output=True,
        prompt_set=prompt_set,
    )


def make_hazard_design_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> HazardDesignSummarizerNode:
    """Create a HazardDesignSummarizerNode with prompt loaded from Jinja2 template."""
    system_prompt = render_prompt(prompt_template, **template_vars)
    return HazardDesignSummarizerNode(
        client=client,
        model=model,
        response_model=HazardSummarizedDesignSpecList,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


def make_hazard_needs_summarizer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[ReviewCacheManager] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> HazardNeedsSummarizerNode:
    """Create a HazardNeedsSummarizerNode with prompt loaded from Jinja2 template."""
    system_prompt = render_prompt(prompt_template, **template_vars)
    return HazardNeedsSummarizerNode(
        client=client,
        model=model,
        response_model=HazardSummarizedUserNeedList,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


__all__ = [
    "dispatch_requirement_reviews",
    "dispatch_hazard_evaluators_early",
    "dispatch_hazard_evaluators_late",
    "RequirementReviewerNode",
    "make_hazard_evaluator_node",
    "make_h6_evaluator_node",
    "make_final_assessor_node",
    "make_hazard_design_summarizer_node",
    "make_hazard_needs_summarizer_node",
]
