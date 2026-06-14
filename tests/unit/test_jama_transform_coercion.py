"""Regression tests for the pyjama -> qaai model coercion in JAMA transforms.

pyjama's transforms emit pyjama's own Requirement/TestCase/DesignDoc classes
(and TestCase uses `in_review_baseline`). qaai's pipeline models (e.g.
TestSuite) require qaai.agents.shared.core classes (`in_baseline`). Without
coercion, downstream construction raises a Pydantic v2 `model_type` error. These
tests assert the qaai transform wrappers return qaai-class instances and that
a TestSuite builds cleanly from them.
"""
import pytest

from qaai.agents.shared import data_integration as di
from qaai.agents.shared.core import (
    DesignDocument as AutoqaDesignDocument,
    Requirement as AutoqaRequirement,
    TestCase as AutoqaTestCase,
)
from qaai.agents.test_suite_reviewer.core import TestSuite

pytestmark = pytest.mark.skipif(
    not di.PYJAMA_AVAILABLE, reason="pyjama not installed"
)


# Mirrors ./cache/source/baselines/BASE-12345 structure (note in_review_baseline).
_SUITE_JAMA = [
    {
        "requirement": {"req_id": "REQ-PUMP-101", "text": "Watchdog latches motor driver."},
        "test_cases": [
            {
                "test_id": "TC-PUMP-201",
                "description": "Nominal heartbeat",
                "setup": "Pump in standard mode",
                "steps": "1. Start infusion.",
                "expectedResults": "Counter increments.",
                "in_review_baseline": False,
            }
        ],
        "design_docs": [
            {"doc_id": "DD-PUMP-RC-001", "name": "Watchdog Arch", "description": "..."}
        ],
    }
]

_CASE_JAMA = [
    {
        "test_case": {
            "test_id": "TC-PUMP-202",
            "description": "Fault injection",
            "setup": "Instrumented build",
            "steps": "1. Suspend task.",
            "expectedResults": "Latches safe.",
            "in_review_baseline": True,
        },
        "requirements": [
            {"req_id": "REQ-PUMP-101", "text": "Watchdog latches motor driver."}
        ],
        "design_docs": [],
    }
]


def test_test_suite_transform_returns_qaai_models():
    states = di.transform_test_suite_review_to_state(_SUITE_JAMA)
    assert len(states) == 1
    entry = states[0]

    assert isinstance(entry["requirement"], AutoqaRequirement)
    assert entry["requirement"].req_id == "REQ-PUMP-101"

    tcs = entry["test_cases"]
    assert tcs and all(isinstance(tc, AutoqaTestCase) for tc in tcs)
    # pyjama's in_review_baseline=False must map onto qaai's in_baseline.
    assert tcs[0].in_baseline is False
    assert tcs[0].test_id == "TC-PUMP-201"

    dds = entry["design_docs"]
    assert dds and all(isinstance(dd, AutoqaDesignDocument) for dd in dds)

    # The crux: TestSuite (qaai) must accept these without a model_type error
    # (summary is unrelated to the bug; an empty list satisfies the field).
    suite = TestSuite(requirement=entry["requirement"], test_cases=entry["test_cases"], summary=[])
    assert suite.requirement.req_id == "REQ-PUMP-101"
    assert len(suite.test_cases) == 1


def test_test_case_transform_returns_qaai_models():
    states = di.transform_test_case_review_to_state(_CASE_JAMA)
    assert len(states) == 1
    entry = states[0]

    assert isinstance(entry["test_case"], AutoqaTestCase)
    assert entry["test_case"].in_baseline is True
    assert all(isinstance(r, AutoqaRequirement) for r in entry["requirements"])
    assert entry["requirements"][0].req_id == "REQ-PUMP-101"


def test_coerce_helper_is_idempotent_on_qaai_entry():
    entry = {
        "requirement": AutoqaRequirement(req_id="REQ-1", text="t"),
        "test_cases": [AutoqaTestCase(test_id="TC-1", description="d", in_baseline=True)],
        "design_docs": [],
    }
    out = di._coerce_state_models_to_qaai(entry)
    assert isinstance(out["requirement"], AutoqaRequirement)
    assert out["test_cases"][0].in_baseline is True
