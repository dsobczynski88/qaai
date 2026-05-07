"""
Node implementations for the hazard risk reviewer.

Per-dimension graph: H1 and H2 evaluate independent hazard fields and run
in parallel from START with `dispatch_requirement_reviews`. After every
parallel `requirement_reviewer` Send fans in, H3 and H4 evaluate the
resulting list of per-requirement SynthesizedAssessment outputs at the
requirement level (not spec-by-spec). H5 joins on H1-H4 and grades
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


# --- per-dimension evaluator nodes (H1-H5) -------------------------------


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
    "severity",
    "exploitability_pre_mitigation",
    "probability_of_harm_pre_mitigation",
    "initial_risk_rating",
    "harm_severity_rationale",
)

_H3_HAZARD_FIELDS = (
    "hazard_id",
    "hazardous_sequence_of_events",
    "software_related_causes",
    "risk_control_measures",
)

_H4_HAZARD_FIELDS = (
    "hazard_id",
    "software_related_causes",
    "risk_control_measures",
    "demonstration_of_effectiveness",
)

_H5_HAZARD_FIELDS = (
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


def _slice_hazard(hazard, fields) -> dict:
    """Return the named scalar fields from a HazardRecord as a plain dict."""
    dump = hazard.model_dump()
    return {k: dump[k] for k in fields if k in dump}


def _summarise_reviews(reviews: List[RequirementReview]) -> List[dict]:
    """Compact requirement-level summary for H3/H4 prompts.

    H3/H4 evaluate at the requirement level, not spec-by-spec. The summary
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


class _H1EvaluatorNode(StandardLLMNode):
    """H1 — Hazard Statement Completeness."""

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        assert hazard is not None
        return _slice_hazard(hazard, _H1_FIELDS)

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> HazardReviewState:
        return {"h1_finding": parsed_result}

    def _get_skip_response(self) -> HazardReviewState:
        return {"h1_finding": None}


class _H2EvaluatorNode(StandardLLMNode):
    """H2 — Pre-Mitigation Risk."""

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        assert hazard is not None
        return _slice_hazard(hazard, _H2_FIELDS)

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> HazardReviewState:
        return {"h2_finding": parsed_result}

    def _get_skip_response(self) -> HazardReviewState:
        return {"h2_finding": None}


class _H3EvaluatorNode(StandardLLMNode):
    """H3 — Risk Control Adequacy. Operates over a list of SynthesizedAssessments."""

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None and state.get("requirement_reviews") is not None

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        reviews = state.get("requirement_reviews") or []
        assert hazard is not None
        payload = _slice_hazard(hazard, _H3_HAZARD_FIELDS)
        payload["requirement_reviews"] = _summarise_reviews(reviews)
        return payload

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> HazardReviewState:
        return {"h3_finding": parsed_result}

    def _get_skip_response(self) -> HazardReviewState:
        return {"h3_finding": None}


class _H4EvaluatorNode(StandardLLMNode):
    """H4 — Verification Depth. Operates over a list of SynthesizedAssessments."""

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None and state.get("requirement_reviews") is not None

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        reviews = state.get("requirement_reviews") or []
        assert hazard is not None
        payload = _slice_hazard(hazard, _H4_HAZARD_FIELDS)
        payload["requirement_reviews"] = _summarise_reviews(reviews)
        return payload

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> HazardReviewState:
        return {"h4_finding": parsed_result}

    def _get_skip_response(self) -> HazardReviewState:
        return {"h4_finding": None}


class _H5EvaluatorNode(StandardLLMNode):
    """H5 — Residual Risk Closure. Sees H1-H4 findings plus post-mitigation fields."""

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        assert hazard is not None
        payload = _slice_hazard(hazard, _H5_HAZARD_FIELDS)
        for key in ("h1_finding", "h2_finding", "h3_finding", "h4_finding"):
            f = state.get(key)
            payload[key] = f.model_dump() if isinstance(f, HazardFinding) else None
        return payload

    def _format_response(self, parsed_result: Optional[HazardFinding]) -> HazardReviewState:
        return {"h5_finding": parsed_result}

    def _get_skip_response(self) -> HazardReviewState:
        return {"h5_finding": None}


# --- final assessor (deterministic verdict + LLM-written prose) ----------


class _FinalAssessorNode(StandardLLMNode):
    """Assembles HazardAssessment from the five upstream HazardFindings.

    The LLM only contributes `comments` and `clarification_questions`; the
    `mandatory_findings` list and `overall_verdict` are computed in code so
    the LLM cannot accidentally re-grade or drop a dimension.
    """

    _CODES = ("H1", "H2", "H3", "H4", "H5")

    def _validate_state(self, state: HazardReviewState) -> bool:
        return state.get("hazard") is not None and all(
            isinstance(state.get(f"h{i}_finding"), HazardFinding) for i in range(1, 6)
        )

    def _build_payload(self, state: HazardReviewState) -> dict:
        hazard = state.get("hazard")
        assert hazard is not None
        return {
            "hazard_id": hazard.hazard_id,
            **{
                f"h{i}_finding": state[f"h{i}_finding"].model_dump()  # type: ignore[index]
                for i in range(1, 6)
            },
        }

    def _format_response(self, parsed_result: Optional[FinalAssessorProse]) -> HazardReviewState:
        # Fallback to empty prose when the LLM call returned None — the
        # deterministic verdict aggregation still proceeds.
        prose = parsed_result or FinalAssessorProse()
        return {"hazard_assessment": self._latest_assessment(prose)}

    def _get_skip_response(self) -> HazardReviewState:
        # Validation failed (one of H1-H5 missing). Return None so callers
        # can detect that the pipeline did not produce a final assessment.
        return {"hazard_assessment": None}

    # ---- deterministic-verdict helpers ----

    def _latest_assessment(self, prose: FinalAssessorProse) -> HazardAssessment:
        # _latest_state is set by __call__ before _format_response runs.
        state = self._latest_state
        hazard = state.get("hazard")
        assert hazard is not None
        findings = [state[f"h{i}_finding"] for i in range(1, 6)]
        return HazardAssessment(
            hazard_id=hazard.hazard_id,
            mandatory_findings=findings,
            overall_verdict=self._aggregate_verdict(findings),
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


def _make_evaluator(
    cls,
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    response_model,
    **template_vars,
):
    system_prompt = render_prompt(prompt_template, **template_vars)
    return cls(
        client=client,
        model=model,
        response_model=response_model,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
    )


def make_h1_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_h1_evaluator-v1.jinja2",
    **template_vars,
) -> _H1EvaluatorNode:
    return _make_evaluator(_H1EvaluatorNode, client, model, model_kwargs, prompt_template, HazardFinding, **template_vars)


def make_h2_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_h2_evaluator-v1.jinja2",
    **template_vars,
) -> _H2EvaluatorNode:
    return _make_evaluator(_H2EvaluatorNode, client, model, model_kwargs, prompt_template, HazardFinding, **template_vars)


def make_h3_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_h3_evaluator-v1.jinja2",
    **template_vars,
) -> _H3EvaluatorNode:
    return _make_evaluator(_H3EvaluatorNode, client, model, model_kwargs, prompt_template, HazardFinding, **template_vars)


def make_h4_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_h4_evaluator-v1.jinja2",
    **template_vars,
) -> _H4EvaluatorNode:
    return _make_evaluator(_H4EvaluatorNode, client, model, model_kwargs, prompt_template, HazardFinding, **template_vars)


def make_h5_evaluator_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_h5_evaluator-v1.jinja2",
    **template_vars,
) -> _H5EvaluatorNode:
    return _make_evaluator(_H5EvaluatorNode, client, model, model_kwargs, prompt_template, HazardFinding, **template_vars)


def make_final_assessor_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str = "hazard_final_assessor-v1.jinja2",
    **template_vars,
) -> _FinalAssessorNode:
    return _make_evaluator(_FinalAssessorNode, client, model, model_kwargs, prompt_template, FinalAssessorProse, **template_vars)


__all__ = [
    "dispatch_requirement_reviews",
    "RequirementReviewerNode",
    "make_requirement_reviewer_node",
    "make_h1_evaluator_node",
    "make_h2_evaluator_node",
    "make_h3_evaluator_node",
    "make_h4_evaluator_node",
    "make_h5_evaluator_node",
    "make_final_assessor_node",
]
