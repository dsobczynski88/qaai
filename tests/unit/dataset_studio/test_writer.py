"""Atomic writes: byte-compatible with the eval harness, all-or-nothing on failure."""

import json

import pytest

from qaai.dataset_studio.writer import (
    ROW_FILES,
    serialize_jsonl,
    write_dataset_atomic,
    write_jsonl_atomic,
)
from qaai.eval.datasets import load_jsonl, write_jsonl

pytestmark = pytest.mark.unit

ROWS = [
    {"index": 0,
     "input": {"requirement": {"req_id": "REQ-1", "text": "SHALL do a thing"}},
     "output": {"synthesized_assessment": {"overall_verdict": "Yes"}},
     "label": {"Overall_Verdict": "Yes"}},
    {"index": 1,
     "input": {"requirement": {"req_id": "REQ-2", "text": "SHALL do another"}},
     "output": {"synthesized_assessment": {"overall_verdict": "No"}},
     "label": {"Overall_Verdict": "No"}},
]


def test_byte_identical_to_the_eval_harness_writer(tmp_path):
    """Pins the format contract: the studio and qaai.eval must agree on file bytes.

    Byte-level, not just parse-level, and deliberately so — including newline
    translation. If the editor wrote LF where write_jsonl writes CRLF, the first save
    would rewrite every line and turn a one-cell correction into a whole-file diff.
    """
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "ünïcode"}]
    theirs, mine = tmp_path / "theirs.jsonl", tmp_path / "mine.jsonl"
    write_jsonl(theirs, rows)
    write_jsonl_atomic(mine, rows)
    assert mine.read_bytes() == theirs.read_bytes()


def test_serialize_keeps_unicode_unescaped():
    assert "ünïcode" in serialize_jsonl([{"b": "ünïcode"}])


def test_serialize_empty_is_empty():
    assert serialize_jsonl([]) == ""


def test_no_temp_files_survive(tmp_path):
    write_jsonl_atomic(tmp_path / "out.jsonl", [{"a": 1}])
    assert [p.name for p in tmp_path.iterdir()] == ["out.jsonl"]


def test_write_dataset_writes_all_three_aligned(tmp_path):
    written = write_dataset_atomic(tmp_path, ROWS)
    assert written == list(ROW_FILES.values())
    counts = {name: len(load_jsonl(tmp_path / name)) for name in written}
    assert set(counts.values()) == {2}
    assert load_jsonl(tmp_path / "actual_labels.jsonl")[1]["Overall_Verdict"] == "No"
    assert load_jsonl(tmp_path / "actual_inputs.jsonl")[0]["requirement"]["req_id"] == "REQ-1"


def test_write_dataset_overwrites_in_place(tmp_path):
    write_dataset_atomic(tmp_path, ROWS)
    write_dataset_atomic(tmp_path, ROWS[:1])
    assert len(load_jsonl(tmp_path / "actual_labels.jsonl")) == 1


def test_missing_section_becomes_an_empty_object(tmp_path):
    """A row with no output section still occupies its line — alignment is the invariant."""
    write_dataset_atomic(tmp_path, [{"index": 0, "input": {"a": 1}, "label": {"b": 2}}])
    assert load_jsonl(tmp_path / "actual_outputs.jsonl") == [{}]


def test_serialization_failure_leaves_the_dataset_untouched(tmp_path):
    """All-or-nothing: an unserializable row in the third file must not half-write."""
    write_dataset_atomic(tmp_path, ROWS)
    before = {name: (tmp_path / name).read_bytes() for name in ROW_FILES.values()}

    class Unserializable:
        pass

    bad = [dict(ROWS[0]), dict(ROWS[1])]
    bad[0] = {**bad[0], "label": {"Overall_Verdict": Unserializable()}}

    with pytest.raises(TypeError):
        write_dataset_atomic(tmp_path, bad)

    after = {name: (tmp_path / name).read_bytes() for name in ROW_FILES.values()}
    assert after == before
    assert not list(tmp_path.glob("*.tmp-*"))


def test_write_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "deeper"
    write_dataset_atomic(target, ROWS)
    assert (target / "actual_inputs.jsonl").exists()


def test_round_trips_through_the_eval_loader(tmp_path):
    """What the editor saves must be what the scorer reads."""
    write_dataset_atomic(tmp_path, ROWS)
    for key, name in ROW_FILES.items():
        assert load_jsonl(tmp_path / name) == [r[key] for r in ROWS]


def test_trailing_newline_exactly_once(tmp_path):
    write_jsonl_atomic(tmp_path / "x.jsonl", [{"a": 1}, {"a": 2}])
    text = (tmp_path / "x.jsonl").read_text(encoding="utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert len(text.splitlines()) == 2
    assert json.loads(text.splitlines()[1]) == {"a": 2}
