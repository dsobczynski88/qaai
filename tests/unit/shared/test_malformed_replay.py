"""Replay regression corpus for LLM schema-fidelity malformations.

Every fixture under ``tests/fixtures/malformed/<Model>/<name>.json`` is a raw LLM
payload that once failed to parse (harvested from ``failed_parse_*`` run dumps by
``scripts/harvest_failed_parses.py``, or curated from a reported shape). Each has a
sibling ``<name>.meta.json`` declaring the target model and whether the current
repair pipeline should recover it (``repairable``).

This test runs each fixture through the REAL parse path
(``BaseLLMNode._parse_llm_response`` → JSON extraction → ``json_repair`` →
``qaai.agents.shared.json_repair_registry.apply_repairs`` → ``model_validate``) and
asserts the declared behavior:

- ``repairable: true``  → the malformation is deterministically recovered to a
  validated model instance (never dropped, never crashed).
- ``repairable: false`` → the payload is genuinely unrecoverable (e.g. empty ``{}``)
  and yields ``None`` — a documented, non-fabricating soft-skip, NOT an exception.

Adding the next malformation is mechanical: drop its dump into the fixtures dir with a
meta file, and this test pins it. No live LLM/network calls.
"""
import json
from pathlib import Path

import pytest

from qaai.agents.shared.core import DecomposedRequirement
from qaai.agents.shared.nodes import BaseLLMNode
from qaai.agents.test_case_reviewer.core import TestCaseAssessment
from qaai.agents.test_suite_reviewer.core import SummarizedTestCaseList

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "malformed"

# Resolve the fixture's declared model name to the actual Pydantic class.
_MODELS = {
    "DecomposedRequirement": DecomposedRequirement,
    "TestCaseAssessment": TestCaseAssessment,
    "SummarizedTestCaseList": SummarizedTestCaseList,
}


def _discover():
    """Yield (id, payload_path, meta) for every malformed fixture on disk."""
    if not _FIXTURE_ROOT.exists():
        return
    for payload_path in sorted(_FIXTURE_ROOT.rglob("*.json")):
        if payload_path.name.endswith(".meta.json"):
            continue
        meta_path = payload_path.with_name(payload_path.stem + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        fixture_id = f"{payload_path.parent.name}/{payload_path.stem}"
        yield pytest.param(payload_path, meta, id=fixture_id)


_CASES = list(_discover())


def _parse(raw: str, model, node_name: str):
    """Drive the real base-node parser with a minimal OpenAI-response shim."""
    class _Msg:
        content = raw

    class _Choice:
        message = _Msg()

    class _Result:
        choices = [_Choice()]

    return BaseLLMNode._parse_llm_response(_Result(), model, node_name)


def test_corpus_is_non_empty():
    """Guard against the glob silently matching nothing (e.g. moved fixtures)."""
    assert _CASES, f"no malformed fixtures discovered under {_FIXTURE_ROOT}"


@pytest.mark.skipif(not _CASES, reason="no malformed fixtures present")
@pytest.mark.parametrize("payload_path, meta", _CASES)
def test_malformed_payload_replays_as_declared(payload_path, meta):
    model_name = meta.get("model")
    model = _MODELS.get(model_name)
    assert model is not None, (
        f"{payload_path.name}: meta 'model' ({model_name!r}) is not a known model; "
        f"add it to _MODELS or fix the meta file."
    )
    repairable = meta.get("repairable")
    assert isinstance(repairable, bool), (
        f"{payload_path.name}: meta must declare a boolean 'repairable'."
    )

    raw = payload_path.read_text(encoding="utf-8")
    result = _parse(raw, model, meta.get("source_node", "replay"))

    if repairable:
        assert result is not None, (
            f"{payload_path.name}: declared repairable but the parse path returned None "
            f"(the repair registry no longer recovers this shape)."
        )
        assert isinstance(result, model)
    else:
        assert result is None, (
            f"{payload_path.name}: declared UNrepairable but the parse path recovered it. "
            f"If a new repair now handles it, flip 'repairable' to true in the meta file."
        )
