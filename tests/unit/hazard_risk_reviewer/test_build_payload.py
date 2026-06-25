"""Payload-construction unit tests for the hazard risk reviewer.

Guards the regression where H2/H3 ran after the design_summarizer but their
LLM payload omitted `summarized_designs` (so the prompt always flagged a
"no summarized_designs" limitation). These tests pin that
HazardEvaluatorNode._build_payload attaches the design summaries for BOTH
H2 and H3 when present in state, and emits an explicit None when absent.

Deterministic — no LLM calls (client is a MagicMock; _build_payload never
touches it).
"""
from unittest.mock import MagicMock

import pytest

from qaai.agents.hazard_risk_reviewer.core import HazardSummarizedDesignSpec
from qaai.agents.hazard_risk_reviewer.nodes import make_hazard_evaluator_node
from qaai.core.config import settings
from tests.conftest import _full_hazard

pytestmark = pytest.mark.unit


_PROMPT_TEMPLATES = {
    "H2": settings.prompt_config.hazard_h2,
    "H3": settings.prompt_config.hazard_h3,
}


def _make_node(dimension_code: str):
    # cache_manager defaults to None -> no cache reads/writes; _build_payload
    # only needs dimension_code + required_fields, so a MagicMock client is fine.
    return make_hazard_evaluator_node(
        dimension_code,
        MagicMock(),
        "stub-model",
        {},
        prompt_template=_PROMPT_TEMPLATES[dimension_code],
    )


def _design_spec(doc_id: str = "DOC-1") -> HazardSummarizedDesignSpec:
    return HazardSummarizedDesignSpec(
        doc_id=doc_id,
        design_intent="Limit report access by role.",
        hazard_controls="Role-based authorization gates report visibility.",
        key_components=["AuthN", "Portal"],
        verification_hooks=["access-denied page"],
        failure_modes=["token expiry"],
    )


@pytest.mark.parametrize("dimension_code", ["H2", "H3"])
def test_build_payload_attaches_summarized_designs(dimension_code):
    node = _make_node(dimension_code)
    designs = [_design_spec("DOC-1"), _design_spec("DOC-2")]
    state = {"hazard": _full_hazard(), "summarized_designs": designs}

    payload = node._build_payload(state)

    assert payload["summarized_designs"] == [d.model_dump() for d in designs]


@pytest.mark.parametrize("dimension_code", ["H2", "H3"])
def test_build_payload_summarized_designs_none_when_absent(dimension_code):
    node = _make_node(dimension_code)
    state = {"hazard": _full_hazard()}  # no summarized_designs in state

    payload = node._build_payload(state)

    assert "summarized_designs" in payload
    assert payload["summarized_designs"] is None
