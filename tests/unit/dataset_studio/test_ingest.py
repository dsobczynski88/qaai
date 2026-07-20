"""Ingesting a completed run into a reviewable dataset.

The property that matters most here is positional alignment: a run's outputs.jsonl can
be shorter than its inputs.jsonl (a graph that raises is skipped before the append), and
the eval harness assumes row i of all three files is the same item.
"""

import json
from pathlib import Path

import pytest

from qaai.dataset_studio.cli import EXIT_OK, EXIT_USAGE, main
from qaai.dataset_studio.editlog import read_edits
from qaai.dataset_studio.ingest import (
    SOURCE_NAME,
    IngestError,
    detect_dataset_type,
    find_run_files,
    ingest_run,
    project_input,
    write_ingested,
)
from qaai.dataset_studio.registry import dataset_type_for, load_type_spec
from qaai.dataset_studio.scaffold import ANSWER_KEY_SUBDIR, DESCRIPTION_NAME, EDITS_LOG_NAME
from qaai.dataset_studio.validate import validate_dataset

pytestmark = pytest.mark.unit


# ── fixtures ────────────────────────────────────────────────────────────────

# The live MandatoryFinding.dimension is an enum, not free text — these are its values.
DIMENSIONS = {
    "M1": "Functional", "M2": "Negative", "M3": "Boundary",
    "M4": "Spec Coverage", "M5": "Terminology", "R6": "Design Alignment",
}


def _rtm_state(req_id, verdict="Yes", cells=None):
    """A full RTM graph state, shaped like a real outputs.jsonl line."""
    codes = cells or {c: "Yes" for c in ("M1", "M2", "M3", "M4", "M5")}
    requirement = {"req_id": req_id, "text": f"The system SHALL {req_id}."}
    return {
        "requirement": requirement,
        "test_cases": [{"test_id": f"TC-{req_id}", "description": "d",
                        "setup": "s", "steps": "1.", "expectedResults": "1."}],
        "decomposed_requirement": {"specs": []},        # extra state, must survive
        "synthesized_assessment": {
            "overall_verdict": verdict,
            "requirement": requirement,
            "comments": "looks fine",
            "clarification_questions": [],
            "mandatory_findings": [
                {"code": c, "verdict": v, "dimension": DIMENSIONS[c],
                 "rationale": f"{c} because", "partial": False,
                 "cited_test_case_ids": [f"TC-{req_id}"], "uncovered_spec_ids": []}
                for c, v in codes.items()
            ],
        },
    }


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def run_dir(tmp_path):
    """A logs/run-<ts>/ folder with three aligned items."""
    d = tmp_path / "logs" / "run-2026-07-20_08-00-00"
    states = [_rtm_state(f"REQ-{i}") for i in (1, 2, 3)]
    _write(d / "outputs.jsonl", states)
    _write(d / "inputs.jsonl",
           [{"requirement": s["requirement"], "test_cases": s["test_cases"]} for s in states])
    return d


# ── discovery ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "names",
    [
        ("inputs.jsonl", "outputs.jsonl"),                      # logs/run-<ts>/
        ("predicted_inputs.jsonl", "predicted_outputs.jsonl"),  # predictions/<ts>/
        ("actual_inputs.jsonl", "actual_outputs.jsonl"),        # an existing dataset
    ],
)
def test_find_run_files_accepts_every_run_convention(tmp_path, names):
    in_name, out_name = names
    _write(tmp_path / in_name, [{"a": 1}])
    _write(tmp_path / out_name, [{"b": 2}])
    found_in, found_out = find_run_files(tmp_path)
    assert found_in.name == in_name
    assert found_out.name == out_name


def test_find_run_files_tolerates_a_missing_inputs_file(tmp_path):
    """Inputs only feed the skipped-items report; outputs are what a dataset is made of."""
    _write(tmp_path / "outputs.jsonl", [{"b": 2}])
    found_in, found_out = find_run_files(tmp_path)
    assert found_in is None
    assert found_out.name == "outputs.jsonl"


def test_find_run_files_rejects_a_folder_with_no_run(tmp_path):
    with pytest.raises(IngestError, match="contains none of"):
        find_run_files(tmp_path)


def test_explicit_paths_override_discovery(tmp_path):
    _write(tmp_path / "outputs.jsonl", [{"decoy": True}])
    chosen = _write(tmp_path / "elsewhere.jsonl", [{"b": 2}])
    _, found_out = find_run_files(tmp_path, outputs=chosen)
    assert found_out == chosen


# ── type detection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "key,expected",
    [
        ("synthesized_assessment", "test_suite"),
        ("aggregated_assessment", "test_case"),
        ("hazard_assessment", "hazard"),
    ],
)
def test_detect_dataset_type_from_the_assessment_key(key, expected):
    assert detect_dataset_type([{key: {"overall_verdict": "Yes"}}]) == expected


def test_detect_dataset_type_scans_past_soft_failed_rows():
    """A run whose first item produced no assessment is still identifiable."""
    rows = [{"requirement": {}}, {}, {"synthesized_assessment": {"overall_verdict": "No"}}]
    assert detect_dataset_type(rows) == "test_suite"


def test_detect_dataset_type_returns_none_when_unrecognisable():
    assert detect_dataset_type([{"something_else": 1}, "not a dict"]) is None


# ── projection ──────────────────────────────────────────────────────────────

def test_project_input_reproduces_the_row_the_graph_was_invoked_with():
    spec = load_type_spec(dataset_type_for("test_suite"))
    state = _rtm_state("REQ-1")
    row = project_input(state, spec)

    assert row["requirement"] == state["requirement"]
    assert row["test_cases"] == state["test_cases"]
    # Absent optional keys stay absent rather than becoming explicit nulls, and
    # non-input state does not leak into the inputs file.
    assert "design_docs" not in row
    assert "decomposed_requirement" not in row
    assert "synthesized_assessment" not in row


# ── the alignment invariant ─────────────────────────────────────────────────

def test_ingest_emits_one_row_per_output_and_keeps_the_files_aligned(run_dir):
    result = ingest_run(run_dir)
    assert result.dataset_type == "test_suite"
    assert result.n_records == 3
    assert [r["index"] for r in result.rows] == [0, 1, 2]
    for row in result.rows:
        assert row["input"]["requirement"]["req_id"] == row["output"]["requirement"]["req_id"]


def test_a_ragged_run_yields_aligned_rows_and_a_skip_report(tmp_path):
    """The bug this guards: services.py appends an output only for items that did not
    raise, so a mid-batch failure shifts inputs and outputs out of step. Zipping them
    positionally would silently label REQ-3's output with REQ-2's input."""
    d = tmp_path / "run-ragged"
    states = [_rtm_state("REQ-1"), _rtm_state("REQ-3")]     # REQ-2 raised mid-batch
    _write(d / "outputs.jsonl", states)
    _write(d / "inputs.jsonl", [
        {"requirement": {"req_id": f"REQ-{i}"}, "test_cases": []} for i in (1, 2, 3)
    ])

    result = ingest_run(d)

    assert result.n_records == 2
    assert [r["input"]["requirement"]["req_id"] for r in result.rows] == ["REQ-1", "REQ-3"]
    assert [s["entity_id"] for s in result.skipped] == ["REQ-2"]
    assert result.provenance["n_skipped"] == 1


def test_labels_are_derived_from_the_outputs_so_the_set_round_trips(run_dir):
    """Labels come from outputs_to_labels — the same function the scorer uses — which
    is what makes check V050 pass by construction rather than by luck."""
    result = ingest_run(run_dir)
    for row in result.rows:
        cells = row["output"]["synthesized_assessment"]["mandatory_findings"]
        assert row["label"]["Overall_Verdict"] == \
            row["output"]["synthesized_assessment"]["overall_verdict"]
        for cell in cells:
            assert row["label"][cell["code"]] == cell["verdict"]


def test_ingest_needs_a_type_it_cannot_detect(tmp_path):
    _write(tmp_path / "outputs.jsonl", [{"mystery": 1}])
    with pytest.raises(IngestError, match="could not tell which reviewer"):
        ingest_run(tmp_path)


def test_an_empty_run_is_refused(tmp_path):
    _write(tmp_path / "outputs.jsonl", [])
    with pytest.raises(IngestError, match="no rows"):
        ingest_run(tmp_path)


# ── writing ─────────────────────────────────────────────────────────────────

def test_write_ingested_lands_a_validating_dataset_under_actual(tmp_path, run_dir):
    result = ingest_run(run_dir, reviewer="tester")
    out = write_ingested(result, base_dir=tmp_path, reviewer="tester")

    assert out.parent.name == ANSWER_KEY_SUBDIR
    assert out.parent.parent.name == "test_suite"

    for name in ("actual_inputs.jsonl", "actual_outputs.jsonl", "actual_labels.jsonl",
                 DESCRIPTION_NAME, EDITS_LOG_NAME, SOURCE_NAME):
        assert (out / name).exists(), name

    report = validate_dataset(out, dataset_type="test_suite")
    assert report.n_errors == 0, report.to_text()


def test_the_ingest_is_recorded_in_the_log_and_the_provenance_sidecar(tmp_path, run_dir):
    result = ingest_run(run_dir, reviewer="tester")
    out = write_ingested(result, base_dir=tmp_path, reviewer="tester")

    records = read_edits(out)
    assert [r.action for r in records] == ["ingest"]
    assert "rows=3" in records[0].note
    assert records[0].by == "tester"

    source = json.loads((out / SOURCE_NAME).read_text(encoding="utf-8"))
    assert source["n_records"] == 3
    assert source["dataset_type"] == "test_suite"
    assert source["source_outputs_sha256"]
    assert source["source_outputs_path"].endswith("outputs.jsonl")


def test_the_description_says_the_labels_are_not_ground_truth(tmp_path, run_dir):
    """Scoring an unreviewed ingest returns 1.000 against the predictions it came from.
    The file has to say so, or someone will report that number."""
    out = write_ingested(ingest_run(run_dir), base_dir=tmp_path)
    text = (out / DESCRIPTION_NAME).read_text(encoding="utf-8")
    assert "UNREVIEWED" in text
    assert "not ground truth" in text
    assert "kappa 0.000" in text          # the grounding rule carries forward


def test_ingest_never_touches_the_source_run(tmp_path, run_dir):
    before = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    write_ingested(ingest_run(run_dir), base_dir=tmp_path)
    assert {p.name: p.read_bytes() for p in run_dir.iterdir()} == before


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_ingest_writes_and_reports(tmp_path, run_dir, capsys):
    code = main(["ingest", str(run_dir), "--base-dir", str(tmp_path), "--quiet"])
    assert code == EXIT_OK

    out = Path(capsys.readouterr().out.strip())
    assert out.is_dir()
    assert out.parent.name == ANSWER_KEY_SUBDIR
    assert len((out / "actual_labels.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 3


def test_cli_ingest_needs_a_source(capsys):
    assert main(["ingest"]) == EXIT_USAGE
    assert "run directory" in capsys.readouterr().err


def test_cli_ingest_reports_an_undetectable_run(tmp_path, capsys):
    _write(tmp_path / "run" / "outputs.jsonl", [{"mystery": 1}])
    assert main(["ingest", str(tmp_path / "run"), "--base-dir", str(tmp_path)]) == EXIT_USAGE
    assert "--type" in capsys.readouterr().err
