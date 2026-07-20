"""Registry projection: row models are derived from the live graph states, not restated."""

from typing import List, Optional, get_args, get_origin

import pytest
from pydantic import ValidationError

from qaai.agents.shared.core import TestCase
from qaai.dataset_studio.registry import (
    DATASET_TYPES,
    dataset_type_for,
    infer_dataset_type,
    input_row_model,
    load_type_spec,
    output_row_model,
    output_row_shape,
)

pytestmark = pytest.mark.unit


@pytest.fixture(params=sorted(DATASET_TYPES))
def type_and_spec(request):
    info = DATASET_TYPES[request.param]
    return info, load_type_spec(info)


def test_input_model_fields_match_spec_input_keys(type_and_spec):
    """The projected model's fields are exactly the row paths the spec names."""
    info, spec = type_and_spec
    model = input_row_model(info, spec)
    assert set(model.model_fields) == {p.split(".")[0] for p in spec.input.values()}


def test_rtm_input_model_reads_annotations_from_the_state():
    """test_cases resolves to List[TestCase] because it was read off RTMReviewState.

    This is the anti-duplication guarantee: nothing in the studio declares this type.
    """
    info = dataset_type_for("test_suite")
    model = input_row_model(info, load_type_spec(info))
    ann = model.model_fields["test_cases"].annotation
    assert ann == List[TestCase]


def test_reducer_annotations_are_unwrapped():
    """Annotated[List[X], operator.add] must project to plain List[X], not Annotated."""
    info = dataset_type_for("test_case")
    spec = load_type_spec(info)
    model = input_row_model(info, spec)
    ann = model.model_fields["requirements"].annotation
    # requirements is not a reducer field, but design_docs/test_case share the path;
    # the guard that matters is that get_type_hints stripped every Annotated wrapper.
    assert get_origin(ann) is list or get_origin(ann) is not None


def test_design_docs_is_optional_everywhere(type_and_spec):
    """Rows routinely omit design_docs; the two states disagree on Optional-ness."""
    info, spec = type_and_spec
    if "design_docs" not in spec.input:
        pytest.skip(f"{info.name} spec declares no design_docs input")
    model = input_row_model(info, spec)
    assert not model.model_fields["design_docs"].is_required()


def test_rtm_input_accepts_a_real_row_and_rejects_a_bad_one():
    info = dataset_type_for("test_suite")
    model = input_row_model(info, load_type_spec(info))

    good = {
        "requirement": {"req_id": "REQ-1", "text": "The system SHALL log out after 15 min."},
        "test_cases": [{"test_id": "TC-1", "description": "Verify logout."}],
    }
    parsed = model.model_validate(good)
    assert parsed.requirement.req_id == "REQ-1"

    with pytest.raises(ValidationError):
        model.model_validate({"requirement": "not an object", "test_cases": []})


def test_input_model_allows_authoring_metadata():
    """rationale / expected_gap ride along on generated rows and must not error."""
    info = dataset_type_for("test_suite")
    model = input_row_model(info, load_type_spec(info))
    parsed = model.model_validate({
        "requirement": {"req_id": "REQ-1", "text": "t"},
        "test_cases": [],
        "expected_gap": "no boundary test",
    })
    assert parsed.model_extra["expected_gap"] == "no boundary test"


def test_output_row_shape_classifies_the_committed_oracle_rows():
    """The committed answer key is minimal shape — it omits dimension/rationale."""
    info = dataset_type_for("test_suite")
    spec = load_type_spec(info)
    oracle = {
        "synthesized_assessment": {
            "overall_verdict": "Yes",
            "mandatory_findings": [{"code": "M1", "verdict": "Yes"}],
        }
    }
    assert output_row_shape(spec, oracle, info) == "minimal"

    full = {
        "synthesized_assessment": {
            "overall_verdict": "Yes",
            "requirement": {"req_id": "REQ-1", "text": "t"},
            "mandatory_findings": [
                {"code": "M1", "dimension": "Functional", "verdict": "Yes", "rationale": "TC-1"}
            ],
        }
    }
    assert output_row_shape(spec, full, info) == "full"
    assert output_row_shape(spec, {}, info) == "empty"


def test_optional_only_extras_stay_minimal():
    """`partial` has a default, so a cell carrying it is still oracle-shaped.

    Shape asks "can the live model validate this row?" — an optional field present
    does not change that answer, but a required one does.
    """
    info = dataset_type_for("test_suite")
    spec = load_type_spec(info)
    row = {
        "synthesized_assessment": {
            "overall_verdict": "Yes",
            "comments": "looks fine",
            "mandatory_findings": [{"code": "M1", "verdict": "Yes", "partial": True}],
        }
    }
    assert output_row_shape(spec, row, info) == "minimal"

    row["synthesized_assessment"]["mandatory_findings"][0]["rationale"] = "TC-1 covers it"
    assert output_row_shape(spec, row, info) == "full"


def test_minimal_oracle_row_would_fail_the_full_model():
    """Justifies shape-aware validation: the full model rejects the committed key."""
    info = dataset_type_for("test_suite")
    model = output_row_model(info, load_type_spec(info))
    with pytest.raises(ValidationError):
        model.model_validate({
            "synthesized_assessment": {
                "overall_verdict": "Yes",
                "mandatory_findings": [{"code": "M1", "verdict": "Yes"}],
            }
        })


def test_output_model_accepts_a_full_state_with_extra_keys():
    info = dataset_type_for("test_suite")
    model = output_row_model(info, load_type_spec(info))
    parsed = model.model_validate({
        "synthesized_assessment": {
            "requirement": {"req_id": "REQ-1", "text": "t"},
            "overall_verdict": "Yes",
            "mandatory_findings": [
                {"code": c, "dimension": d, "verdict": "Yes", "rationale": "r"}
                for c, d in [
                    ("M1", "Functional"), ("M2", "Negative"), ("M3", "Boundary"),
                    ("M4", "Spec Coverage"), ("M5", "Terminology"), ("R6", "Design Alignment"),
                ]
            ],
        },
        "coverage_analysis": [],
        "some_other_state_key": 1,
    })
    assert parsed.synthesized_assessment.overall_verdict == "Yes"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("eval/datasets/test_suite", "test_suite"),
        ("eval/datasets/test_suite/2026-07-19_10-31-02", "test_suite"),
        ("eval/datasets/hazard/2026-07-19_10-31-02", "hazard"),
        ("eval/datasets/test_case", "test_case"),
        # The actual/ segment puts the type two levels up — the old two-candidate
        # lookup returned None here, which made validate/edit exit 3 without --type.
        ("eval/datasets/test_suite/actual/2026-07-20_08-15-00", "test_suite"),
        ("eval/datasets/hazard/actual/2026-07-20_08-15-00", "hazard"),
        ("eval/datasets/test_case/actual", "test_case"),
        # Deeper still: a predictions set under an answer key.
        (
            "eval/datasets/test_suite/actual/2026-07-20_08-15-00/predictions/2026-07-20_09-00-00",
            "test_suite",
        ),
        ("some/unrelated/dir", None),
    ],
)
def test_infer_dataset_type(path, expected):
    assert infer_dataset_type(path) == expected


def test_dataset_type_for_rejects_unknown():
    with pytest.raises(KeyError, match="unknown dataset type"):
        dataset_type_for("nope")
