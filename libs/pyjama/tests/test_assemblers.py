"""Unit tests for typekey normalization and assembler classification.

Credential-free: these exercise the pure assembly/transform logic with
hand-built Jama-shaped item dicts (no Jama API, no .env required).

Regression coverage for the bug where a list-valued ``design_typekey`` reached
``TestCaseReviewerAssembler._separate_upstream_items`` and raised
``TypeError: 'in <string>' requires string as left operand, not list`` at
``elif design_typekey in doc_key``. The fix normalizes every typekey argument to
a list of substring matchers, so a str OR a list now both work — and multiple
design typekeys are supported.
"""
import pytest

from pyjama.utils.jama_utils import normalize_typekeys, get_doc_key
from pyjama.utils.jama_constants import (
    FIELDS_KEY,
    DOCUMENT_KEY,
    DESCRIPTION_KEY,
    NAME_KEY,
    REQUIREMENT_ID_KEY,
    DOC_ID_KEY,
    REQUIREMENTS_KEY,
    DESIGN_DOCS_KEY,
    TEST_CASES_KEY,
    DEFAULT_REQ_TYPEKEYS,
)
from pyjama.assemblers.jama_assemblers import (
    TestCaseReviewerAssembler,
    BidirectionalTraceAssembler,
)


# ---------------------------------------------------------------------------
# Item builders (minimal Jama-shaped payloads)
# ---------------------------------------------------------------------------
def _item(item_id: int, doc_key: str, **fields):
    """Build a minimal Jama item with a documentKey and optional extra fields."""
    return {"id": item_id, FIELDS_KEY: {DOCUMENT_KEY: doc_key, **fields}}


# ---------------------------------------------------------------------------
# normalize_typekeys
# ---------------------------------------------------------------------------
class TestNormalizeTypekeys:
    def test_string_becomes_single_element_list(self):
        assert normalize_typekeys("DES") == ["DES"]

    def test_list_passthrough(self):
        assert normalize_typekeys(["DES", "DESIGN"]) == ["DES", "DESIGN"]

    def test_tuple_becomes_list(self):
        assert normalize_typekeys(("REQ", "PRQ")) == ["REQ", "PRQ"]

    def test_none_uses_default(self):
        assert normalize_typekeys(None, ["DES"]) == ["DES"]

    def test_none_default_tuple(self):
        assert normalize_typekeys(None, DEFAULT_REQ_TYPEKEYS) == ["REQ", "PRQ"]

    def test_empty_string_uses_default(self):
        assert normalize_typekeys("", ["DES"]) == ["DES"]

    def test_none_without_default_is_empty(self):
        assert normalize_typekeys(None) == []

    def test_drops_empty_entries(self):
        assert normalize_typekeys(["DES", "", None]) == ["DES"]


# ---------------------------------------------------------------------------
# get_doc_key hardening
# ---------------------------------------------------------------------------
class TestGetDocKey:
    def test_reads_document_key_from_fields(self):
        assert get_doc_key(_item(1, "REQ-9")) == "REQ-9"

    def test_missing_returns_empty_string(self):
        assert get_doc_key({"id": 1, FIELDS_KEY: {}}) == ""

    def test_non_string_is_coerced_not_raised(self):
        # A malformed payload must never crash downstream substring matching.
        item = {"id": 1, FIELDS_KEY: {DOCUMENT_KEY: ["REQ-9"]}}
        result = get_doc_key(item)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestCaseReviewerAssembler.assemble
# ---------------------------------------------------------------------------
class TestTestCaseReviewerAssembler:
    def _inputs(self):
        test_cases_dict = {100: _item(100, "TC-1", **{NAME_KEY: "a test"})}
        upstream_results = {
            100: [
                _item(200, "REQ-9", **{DESCRIPTION_KEY: "a requirement"}),
                _item(300, "DES-7", **{DESCRIPTION_KEY: "a design doc"}),
            ]
        }
        return test_cases_dict, upstream_results

    def test_design_typekey_as_list_does_not_raise_and_classifies(self):
        """Regression: a list-valued design_typekey previously raised TypeError."""
        test_cases_dict, upstream_results = self._inputs()
        result = TestCaseReviewerAssembler().assemble(
            test_cases_dict=test_cases_dict,
            upstream_results=upstream_results,
            requirement_typekeys=["REQ", "PRQ"],
            design_typekey=["DES", "DESIGN"],  # <-- the input that used to crash
        )
        assert len(result) == 1
        entry = result[0]
        assert [r[REQUIREMENT_ID_KEY] for r in entry[REQUIREMENTS_KEY]] == ["REQ-9"]
        assert [d[DOC_ID_KEY] for d in entry[DESIGN_DOCS_KEY]] == ["DES-7"]

    def test_design_typekey_as_string_still_works(self):
        """Backward compatibility: a single-string design_typekey behaves as before."""
        test_cases_dict, upstream_results = self._inputs()
        result = TestCaseReviewerAssembler().assemble(
            test_cases_dict=test_cases_dict,
            upstream_results=upstream_results,
            requirement_typekeys=["REQ", "PRQ"],
            design_typekey="DES",
        )
        entry = result[0]
        assert [r[REQUIREMENT_ID_KEY] for r in entry[REQUIREMENTS_KEY]] == ["REQ-9"]
        assert [d[DOC_ID_KEY] for d in entry[DESIGN_DOCS_KEY]] == ["DES-7"]

    def test_multiple_design_typekeys_match_alternate_key(self):
        """Feature: a second design typekey classifies docs under that key."""
        test_cases_dict = {100: _item(100, "TC-1")}
        upstream_results = {100: [_item(300, "DESIGN-7")]}
        result = TestCaseReviewerAssembler().assemble(
            test_cases_dict=test_cases_dict,
            upstream_results=upstream_results,
            requirement_typekeys=["REQ", "PRQ"],
            design_typekey=["DES", "DESIGN"],
        )
        entry = result[0]
        assert entry[REQUIREMENTS_KEY] == []
        assert [d[DOC_ID_KEY] for d in entry[DESIGN_DOCS_KEY]] == ["DESIGN-7"]


# ---------------------------------------------------------------------------
# PyJamaRequest model accepts str or list for typekeys
# ---------------------------------------------------------------------------
class TestPyJamaRequestTypekeys:
    def test_design_typekey_accepts_list(self):
        from pyjama.langgraph.nodes import PyJamaRequest

        req = PyJamaRequest(
            request_type="test_case_review",
            baseline_id="BASE-1",
            design_typekey=["DES", "DESIGN"],
        )
        assert req.design_typekey == ["DES", "DESIGN"]

    def test_design_typekey_accepts_string(self):
        from pyjama.langgraph.nodes import PyJamaRequest

        req = PyJamaRequest(
            request_type="test_case_review",
            baseline_id="BASE-1",
            design_typekey="DES",
        )
        assert req.design_typekey == "DES"

    def test_user_need_typekey_accepts_list(self):
        from pyjama.langgraph.nodes import PyJamaRequest

        req = PyJamaRequest(
            request_type="bidirectional_trace",
            project_name="proj",
            identifiers=["GID-1"],
            user_need_typekey=["UND", "USER-NEED"],
        )
        assert req.user_need_typekey == ["UND", "USER-NEED"]


# ---------------------------------------------------------------------------
# BidirectionalTraceAssembler.assemble (hazard / bidirectional trace path)
# ---------------------------------------------------------------------------
class TestBidirectionalTraceAssembler:
    """Regression for the hazard pipeline crash: a list-valued design_typekey
    reached the bidirectional downstream split and raised
    ``TypeError: 'in <string>' requires string as left operand, not list``."""

    def _assemble(self, design_typekey):
        sw_req = _item(1, "REQ-1", **{DESCRIPTION_KEY: "a requirement"})
        design = _item(300, "DES-7", **{DESCRIPTION_KEY: "a design doc"})
        return BidirectionalTraceAssembler().assemble(
            software_reqs_dict={1: sw_req},
            software_upstream_results={1: []},
            software_downstream_results={1: [design]},
            system_reqs_dict={},
            system_upstream_results={},
            user_needs_dict={},
            test_cases_dict={},
            design_docs_dict={300: design},
            design_typekey=design_typekey,  # <-- list used to crash the downstream split
        )

    def test_design_typekey_as_list_does_not_raise_and_classifies(self):
        result = self._assemble(["DES", "DESIGN"])
        assert len(result) == 1
        entry = result[0]
        assert entry[TEST_CASES_KEY] == []
        assert [d[DOC_ID_KEY] for d in entry[DESIGN_DOCS_KEY]] == ["DES-7"]

    def test_design_typekey_as_string_still_works(self):
        result = self._assemble("DES")
        assert [d[DOC_ID_KEY] for d in result[0][DESIGN_DOCS_KEY]] == ["DES-7"]
