"""Unit tests for eval-spec loading and dotted-path extraction (no LLM)."""
import pytest

from qaai.eval.spec import get_path, load_spec

pytestmark = pytest.mark.unit

SPEC_NAMES = ["test_suite_reviewer", "hazard_risk_reviewer", "test_case_reviewer"]


def _rtm():
    return load_spec("eval/specs/test_suite_reviewer.yaml")


@pytest.mark.parametrize("name", SPEC_NAMES)
def test_all_shipped_specs_load(name):
    spec = load_spec(f"eval/specs/{name}.yaml")
    assert spec.component == name
    assert spec.output.verdict_path
    assert spec.output.rubric and spec.output.rubric.codes


def test_mandatory_codes_exclude_advisory():
    spec = _rtm()
    assert spec.mandatory_codes == ["M1", "M2", "M3", "M4", "M5"]
    assert "R6" not in spec.mandatory_codes


def test_extract_prediction_from_dict():
    spec = _rtm()
    row = {
        "synthesized_assessment": {
            "overall_verdict": "Yes",
            "mandatory_findings": [
                {"code": "M1", "verdict": "Yes"},
                {"code": "M2", "verdict": "N-A"},
            ],
        }
    }
    verdict, rubric = spec.extract_prediction(row)
    assert verdict == "Yes"
    assert rubric == {"M1": "Yes", "M2": "N-A"}


def test_extract_label_flat():
    spec = _rtm()
    verdict, rubric = spec.extract_label({"Overall_Verdict": "No", "M1": "Yes", "M4": "No"})
    assert verdict == "No"
    assert rubric["M1"] == "Yes" and rubric["M4"] == "No"


def test_get_path_handles_pydantic_models():
    from qaai.agents.shared.core import Requirement

    state = {"requirement": Requirement(req_id="REQ-1", text="t")}
    assert get_path(state, "requirement.req_id") == "REQ-1"


def test_get_path_missing_returns_none():
    assert get_path({"a": {"b": 1}}, "a.c.d") is None


def test_tc_spec_uses_id_code_field():
    spec = load_spec("eval/specs/test_case_reviewer.yaml")
    assert spec.output.rubric.code_field == "id"
    assert "expected_result_support" in spec.output.rubric.codes
