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
"""

from typing import Any, List, Optional

from langgraph.types import Send

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.shared.nodes import BaseLLMNode, StandardLLMNode
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.config import settings
from autoqa.prj_logger import ProjectLogger
from autoqa.utils import render_prompt

from .core import (
    FinalAssessorProse,
    HazardAssessment,
    HazardFinding,
    HazardReviewState,
    RequirementReview,
)

project_logger = ProjectLogger(name="logger.hazard.nodes", log_file=settings.log_file_path)
project_logger.config()
logger = project_logger.get_logger()


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
    
    return [
        Send("h1_evaluator", {"hazard": hazard}),
        Send("h2_evaluator", {"hazard": hazard}),
        Send("h3_evaluator", {"hazard": hazard}),
        Send("h7_evaluator", {"hazard": hazard}),
    ]


def dispatch_hazard_evaluators_late(state: HazardReviewState) -> List[Send]:
    """
    Dispatch H4, H5 after requirement_reviews complete.
    These need the requirement_reviews list.
    """
    hazard = state.get("hazard")
    requirement_reviews = state.get("requirement_reviews")
    
    if not hazard or not requirement_reviews:
        logger.warning("dispatch_hazard_evaluators_late: missing hazard or requirement_reviews")
        return []
    
    return [
        Send("h4_evaluator", {"hazard": hazard, "requirement_reviews": requirement_reviews}),
        Send("h5_evaluator", {"hazard": hazard, "requirement_reviews": requirement_reviews}),
    ]


# --- dispatcher + RTM fan-out (unchanged from prior architecture) ---------


def dispatch_requirement_reviews(state: HazardReviewState) -> List[Send]:
    """
    LangGraph Send dispatcher: fans out one Send per traced requirement so
    each requirement is reviewed in parallel by RequirementReviewerNode.
    Returns an empty list when the hazard or its requirements are missing.
    """
    hazard = state.get("hazard")
    if not hazard or not hazard.requirements:
        logger.warning("dispatch_requirement_reviews: no hazard/requirements, skipping fan-out")
        return []
    return [
        Send("requirement_reviewer", {"hazard": hazard, "requirement": req})
        for req in hazard.requirements
    ]


class RequirementReviewerNode:
    """
    Invokes the entire compiled test_suite_reviewer graph as an atomic
    subgraph for one requirement. The wrapped RTMReviewerRunnable is shared
    across all parallel Send fan-outs — its compiled graph is built once and
    reused per requirement.
    """

    def __init__(self, rtm_runnable: RTMReviewerRunnable):
        self.rtm = rtm_runnable

    async def __call__(self, state: Any) -> HazardReviewState:
        hazard = state.get("hazard")
        requirement = state.get("requirement")
        if hazard is None or requirement is None:
            logger.warning("RequirementReviewerNode: missing hazard or requirement, skipping")
            return {"requirement_reviews": []}

        rtm_input = {
            "requirement": requirement,
            "test_cases": hazard.test_cases,
        }
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
        return {"requirement_reviews": [review]}


def make_requirement_reviewer_node(rtm_runnable: RTMReviewerRunnable) -> RequirementReviewerNode:
    """Wrap a shared RTMReviewerRunnable so each Send invokes the same compiled subgraph."""
    return RequirementReviewerNode(rtm_runnable)


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
    ):
        super().__init__(client, model, HazardFinding, system_prompt, model_kwargs)
        self.dimension_code = dimension_code
        self.required_fields = required_fields

    def _validate_state(self, state: Any) -> bool:
        # H1, H2, H3, H7 only need hazard
        if self.dimension_code in ["H1", "H2", "H3", "H7"]:
            return state.get("hazard") is not None
        # H4, H5 need hazard + requirement_reviews
        return (
            state.get("hazard") is not None
            and state.get("requirement_reviews") is not None
        )

    def _build_payload(self, state: Any) -> dict:
        hazard = state["hazard"]
        payload = _slice_hazard(hazard, self.required_fields)
        
        # H4, H5 need requirement_reviews
        if self.dimension_code in ["H4", "H5"]:
            reviews = state.get("requirement_reviews", [])
            payload["requirement_reviews"] = _summarise_reviews(reviews)
            # H4, H5 also get the full requirements and test_cases lists
            payload["requirements"] = [r.requirement.model_dump() for r in reviews]
            payload["test_cases"] = [tc.model_dump() for tc in hazard.test_cases]
        
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

    async def __call__(self, state: Any) -> Any:
        # Custom flow: when the upstream H1-H5 findings are all present we
        # always produce a HazardAssessment, even if the LLM prose call
        # fails or returns unparseable JSON (deterministic verdict
        # aggregation does not depend on the LLM). The base StandardLLMNode
        # would short-circuit to _get_skip_response on parse failure, which
        # would silently drop the assessment.
        self._latest_state = state
        if not self._validate_state(state):
            return self._get_skip_response()
        try:
            payload = self._build_payload(state)
        except Exception as e:
            logger.warning("%s: payload building failed — %s", self.__class__.__name__, e)
            return self._format_response(None)
        import json as _json
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": _json.dumps(payload)},
        ]
        try:
            result = await self.client.chat_completion(
                model=self.model, messages=messages, **self.model_kwargs,
            )
            parsed = self._parse_llm_response(result, self.response_model, self.__class__.__name__)
        except Exception as e:
            logger.warning("%s: LLM call failed — %s", self.__class__.__name__, e)
            parsed = None
        return self._format_response(parsed)


# --- factories ------------------------------------------------------------


def _make_hazard_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    dimension_code: str,
    required_fields: tuple,
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
    )


def _make_h6_evaluator(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
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
    )


def make_h1_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H1",
        required_fields=_H1_FIELDS,
        **template_vars,
    )


def make_h2_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H2",
        required_fields=_H2_FIELDS,
        **template_vars,
    )


def make_h3_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H3",
        required_fields=_H3_FIELDS,
        **template_vars,
    )


def make_h4_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H4",
        required_fields=_H4_FIELDS,
        **template_vars,
    )


def make_h5_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H5",
        required_fields=_H5_FIELDS,
        **template_vars,
    )


def make_h6_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> H6EvaluatorNode:
    return _make_h6_evaluator(
        client, model, model_kwargs, prompt_template,
        **template_vars,
    )


def make_h7_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> HazardEvaluatorNode:
    return _make_hazard_evaluator(
        client, model, model_kwargs, prompt_template,
        dimension_code="H7",
        required_fields=_H7_FIELDS,
        **template_vars,
    )


def make_final_assessor_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    **template_vars,
) -> _FinalAssessorNode:
    system_prompt = render_prompt(prompt_template, **template_vars)
    return _FinalAssessorNode(
        client=client,
        model=model,
        response_model=FinalAssessorProse,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
    )


__all__ = [
    "dispatch_requirement_reviews",
    "dispatch_hazard_evaluators_early",
    "dispatch_hazard_evaluators_late",
    "RequirementReviewerNode",
    "make_requirement_reviewer_node",
    "make_h1_evaluator_node",
    "make_h2_evaluator_node",
    "make_h3_evaluator_node",
    "make_h4_evaluator_node",
    "make_h5_evaluator_node",
    "make_h6_evaluator_node",
    "make_h7_evaluator_node",
    "make_final_assessor_node",
]
