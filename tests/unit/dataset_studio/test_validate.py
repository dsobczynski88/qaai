"""Validator behavior, driven by mutating a known-good in-memory dataset.

The first test is the important one: the committed 20-row answer key must validate
clean. It is the repo's only grounded dataset, and a validator that flags it is
wrong about the schema, not the other way round.
"""

import copy
import json
from pathlib import Path

import pytest

from qaai.dataset_studio.cli import (
    EXIT_FINDINGS,
    EXIT_MISSING,
    EXIT_OK,
    EXIT_USAGE,
    main,
)
from qaai.dataset_studio.registry import dataset_type_for, load_type_spec
from qaai.dataset_studio.validate import validate_dataset, validate_rows

pytestmark = pytest.mark.unit

COMMITTED = Path("eval/datasets/test_suite/actual/2026-07-17_12-01-00")
CODES = ["M1", "M2", "M3", "M4", "M5"]
DIMS = {
    "M1": "Functional", "M2": "Negative", "M3": "Boundary",
    "M4": "Spec Coverage", "M5": "Terminology", "R6": "Design Alignment",
}


@pytest.fixture
def spec():
    return load_type_spec(dataset_type_for("test_suite"))


def _codes(report, severity=None):
    return [f.code for f in report.findings if severity is None or f.severity == severity]


# ── the regression guard ────────────────────────────────────────────────────

def test_committed_answer_key_validates_clean():
    report = validate_dataset(COMMITTED, dataset_type="test_suite")
    assert report.n_errors == 0, report.to_text(0)
    assert report.row_counts == {
        "actual_inputs.jsonl": 20,
        "actual_outputs.jsonl": 20,
        "actual_labels.jsonl": 20,
    }


def test_committed_answer_key_exits_zero_via_cli(capsys):
    assert main(["validate", str(COMMITTED), "--type", "test_suite"]) == EXIT_OK
    assert "no findings" in capsys.readouterr().out


# ── a synthetic good dataset to mutate ──────────────────────────────────────

def _good(n=4):
    """n rows, half Yes / half No, minimal (oracle) output shape."""
    inputs, outputs, labels = [], [], []
    for i in range(n):
        bad = i >= n // 2
        cells = {c: "Yes" for c in CODES}
        if bad:
            cells["M4"] = "No"
        verdict = "No" if bad else "Yes"
        inputs.append({
            "requirement": {"req_id": f"REQ-{i:03d}", "text": f"The system SHALL do thing {i}."},
            "test_cases": [{"test_id": f"TC-{i:03d}-A", "description": "Verify the thing."}],
        })
        outputs.append({
            "synthesized_assessment": {
                "overall_verdict": verdict,
                "mandatory_findings": [{"code": c, "verdict": v} for c, v in cells.items()],
            }
        })
        labels.append({"Overall_Verdict": verdict, **cells})
    return inputs, outputs, labels


def _run(spec, inputs, outputs, labels, **kw):
    return validate_rows("test_suite", spec, inputs, outputs, labels, **kw)


def test_synthetic_good_dataset_is_clean(spec):
    report = _run(spec, *_good())
    assert report.n_errors == 0, report.to_text(0)


def test_v002_row_count_mismatch(spec):
    inputs, outputs, labels = _good()
    outputs.pop()
    assert "V002" in _codes(_run(spec, inputs, outputs, labels), "error")


def test_v010_bad_input_type(spec):
    inputs, outputs, labels = _good()
    inputs[0]["test_cases"] = "not a list"
    report = _run(spec, inputs, outputs, labels)
    assert "V010" in _codes(report, "error")


def test_v021_missing_assessment(spec):
    inputs, outputs, labels = _good()
    outputs[1] = {}
    assert "V021" in _codes(_run(spec, inputs, outputs, labels), "error")


def test_v030_unknown_label_key(spec):
    inputs, outputs, labels = _good()
    labels[0]["M9"] = "Yes"
    report = _run(spec, inputs, outputs, labels)
    assert "V030" in _codes(report, "error")


def test_v030_allows_reviewer_metadata(spec):
    """The editor writes reviewer_note / reviewed_by / reviewed_at into the labels row."""
    inputs, outputs, labels = _good()
    labels[0].update({
        "reviewer_note": "Agree - TC-000-A exercises the stated threshold directly.",
        "reviewed_by": "dsobc",
        "reviewed_at": "2026-07-19T10:35:02.114-05:00",
        "class": "known_good",
    })
    assert _run(spec, inputs, outputs, labels).n_errors == 0


def test_v031_na_on_a_code_that_forbids_it(spec):
    inputs, outputs, labels = _good()
    labels[0]["M1"] = "N-A"
    outputs[0]["synthesized_assessment"]["mandatory_findings"][0]["verdict"] = "N-A"
    report = _run(spec, inputs, outputs, labels)
    assert "V031" in _codes(report, "error")


def test_v031_allows_na_on_m2_m3(spec):
    inputs, outputs, labels = _good()
    for f in outputs[0]["synthesized_assessment"]["mandatory_findings"]:
        if f["code"] in ("M2", "M3"):
            f["verdict"] = "N-A"
    labels[0]["M2"] = labels[0]["M3"] = "N-A"
    assert "V031" not in _codes(_run(spec, inputs, outputs, labels))


def test_v040_output_verdict_contradicts_its_cells(spec):
    inputs, outputs, labels = _good()
    # Cells say M4=No but the stated verdict claims Yes.
    outputs[0]["synthesized_assessment"]["mandatory_findings"][3]["verdict"] = "No"
    report = _run(spec, inputs, outputs, labels)
    v040 = [f for f in report.findings if f.code == "V040"]
    assert v040
    assert any(f.file == "actual_outputs.jsonl" for f in v040)
    assert any("M4=No" in f.message for f in v040)


def test_v040_label_verdict_contradicts_the_cells(spec):
    inputs, outputs, labels = _good()
    labels[0]["Overall_Verdict"] = "No"  # cells all say Yes
    report = _run(spec, inputs, outputs, labels)
    assert any(
        f.code == "V040" and f.file == "actual_labels.jsonl" for f in report.findings
    )


def test_v040_ignores_advisory_r6(spec):
    """An R6=No must never flip the derived verdict."""
    inputs, outputs, labels = _good()
    outputs[0]["synthesized_assessment"]["mandatory_findings"].append(
        {"code": "R6", "verdict": "No"}
    )
    labels[0]["R6"] = "No"
    assert "V040" not in _codes(_run(spec, inputs, outputs, labels))


def test_v041_missing_mandatory_cell(spec):
    inputs, outputs, labels = _good()
    outputs[0]["synthesized_assessment"]["mandatory_findings"].pop()  # drop M5
    del labels[0]["M5"]
    report = _run(spec, inputs, outputs, labels)
    assert any(f.code == "V041" and "M5" in f.message for f in report.findings)


def test_v041_absent_advisory_r6_is_fine(spec):
    """The committed answer key has no R6 column; that must stay legal."""
    assert "V041" not in _codes(_run(spec, *_good()))


def test_v041_out_of_order_cells(spec):
    inputs, outputs, labels = _good()
    findings = outputs[0]["synthesized_assessment"]["mandatory_findings"]
    findings[0], findings[1] = findings[1], findings[0]
    report = _run(spec, inputs, outputs, labels)
    assert any(f.code == "V041" and "order" in f.message for f in report.findings)


def test_v050_label_and_output_disagree_on_a_cell(spec):
    inputs, outputs, labels = _good()
    labels[0]["M2"] = "No"  # output still says Yes
    report = _run(spec, inputs, outputs, labels)
    v050 = [f for f in report.findings if f.code == "V050"]
    assert v050 and v050[0].path == "M2"


def test_v060_partial_without_yes(spec):
    inputs, outputs, labels = _good()
    outputs[0]["synthesized_assessment"]["mandatory_findings"][3].update(
        {"verdict": "No", "partial": True}
    )
    labels[0]["M4"] = "No"
    labels[0]["Overall_Verdict"] = "No"
    assert "V060" in _codes(_run(spec, inputs, outputs, labels), "warning")


def test_v070_duplicate_primary_id(spec):
    inputs, outputs, labels = _good()
    inputs[1]["requirement"]["req_id"] = inputs[0]["requirement"]["req_id"]
    report = _run(spec, inputs, outputs, labels)
    assert any(f.code == "V070" and "duplicate" in f.message for f in report.findings)


def test_v071_citation_to_a_nonexistent_test_case(spec):
    """Full-shape rows only — this is what catches a reviewer deleting a cited TC."""
    inputs, outputs, labels = _good()
    outputs[0]["synthesized_assessment"]["mandatory_findings"][0].update(
        {"dimension": "Functional", "rationale": "r", "cited_test_case_ids": ["TC-GHOST"]}
    )
    report = _run(spec, inputs, outputs, labels)
    assert any(f.code == "V071" and "TC-GHOST" in f.message for f in report.findings)


def test_citation_checks_are_silent_on_minimal_rows(spec):
    """Minimal oracle rows have no citation fields by construction."""
    report = _run(spec, *_good())
    assert "V061" not in _codes(report)
    assert "V071" not in _codes(report)


def test_v090_single_class_dataset(spec):
    inputs, outputs, labels = _good()
    for out, lab in zip(outputs, labels):
        for f in out["synthesized_assessment"]["mandatory_findings"]:
            f["verdict"] = "Yes"
        out["synthesized_assessment"]["overall_verdict"] = "Yes"
        lab.update({"Overall_Verdict": "Yes", **{c: "Yes" for c in CODES}})
    report = _run(spec, inputs, outputs, labels)
    assert any(f.code == "V090" and "single-class" in f.message for f in report.findings)


# ── selection flags ─────────────────────────────────────────────────────────

def test_row_range_narrows_the_findings(spec):
    inputs, outputs, labels = _good()
    labels[3]["Overall_Verdict"] = "Yes"  # row 3 now contradicts its cells
    assert _run(spec, inputs, outputs, labels).n_errors > 0
    assert _run(spec, inputs, outputs, labels, row_range=range(0, 2)).n_errors == 0


def test_skip_suppresses_a_check(spec):
    inputs, outputs, labels = _good()
    labels[0]["Overall_Verdict"] = "No"
    assert "V040" in _codes(_run(spec, inputs, outputs, labels))
    assert "V040" not in _codes(_run(spec, inputs, outputs, labels, skip={"V040"}))


# ── CLI surface ─────────────────────────────────────────────────────────────

def _write_dataset(d: Path, inputs, outputs, labels):
    d.mkdir(parents=True, exist_ok=True)
    for name, rows in [
        ("actual_inputs.jsonl", inputs),
        ("actual_outputs.jsonl", outputs),
        ("actual_labels.jsonl", labels),
    ]:
        (d / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
    return d


def test_cli_exit_1_on_errors(tmp_path, capsys):
    inputs, outputs, labels = _good()
    labels[0]["Overall_Verdict"] = "No"
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", inputs, outputs, labels)
    assert main(["validate", str(d)]) == EXIT_FINDINGS
    assert "V040" in capsys.readouterr().out


def test_cli_infers_type_from_a_timestamped_path(tmp_path):
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    assert main(["validate", str(d)]) == EXIT_OK


def test_cli_exit_2_when_missing(tmp_path):
    assert main(["validate", str(tmp_path / "nope"), "--type", "test_suite"]) == EXIT_MISSING


def test_cli_exit_3_on_uninferable_type(tmp_path):
    d = _write_dataset(tmp_path / "mystery", *_good())
    assert main(["validate", str(d)]) == EXIT_USAGE


def test_cli_exit_3_on_unknown_check_code(tmp_path):
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    assert main(["validate", str(d), "--checks", "V999"]) == EXIT_USAGE


def test_cli_strict_promotes_warnings(tmp_path):
    inputs, outputs, labels = _good()
    inputs[1]["requirement"]["req_id"] = inputs[0]["requirement"]["req_id"]  # V070 = error
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", inputs, outputs, labels)
    assert main(["validate", str(d)]) == EXIT_FINDINGS

    # A dataset whose only findings are warnings: partial=true beside a No verdict.
    inputs, outputs, labels = _good()
    assessment = outputs[0]["synthesized_assessment"]
    assessment["mandatory_findings"][3].update({"verdict": "No", "partial": True})
    assessment["overall_verdict"] = "No"
    labels[0].update({"M4": "No", "Overall_Verdict": "No"})
    d2 = _write_dataset(tmp_path / "test_suite" / "2026-07-19_11-00-00", inputs, outputs, labels)
    assert main(["validate", str(d2)]) == EXIT_OK
    assert main(["validate", str(d2), "--strict"]) == EXIT_FINDINGS


def test_cli_exit_4_on_a_missing_spec(tmp_path, capsys):
    """A bad spec is an authoring mistake in eval/specs/, distinct from a bad dataset."""
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    assert main(["validate", str(d), "--spec", str(tmp_path / "nope.yaml")]) == 4
    assert "eval spec" in capsys.readouterr().err


def test_cli_exit_4_on_an_invalid_spec(tmp_path, capsys):
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    bad_spec = tmp_path / "bad.yaml"
    bad_spec.write_text("name: x\n", encoding="utf-8")  # missing required keys
    assert main(["validate", str(d), "--spec", str(bad_spec)]) == 4


def test_cli_json_output_parses(tmp_path, capsys):
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    assert main(["validate", str(d), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset_type"] == "test_suite"
    assert [f for f in payload["findings"] if f["severity"] == "error"] == []


def test_cli_invalid_json_line_reports_v001(tmp_path, capsys):
    d = _write_dataset(tmp_path / "test_suite" / "2026-07-19_10-00-00", *_good())
    (d / "actual_labels.jsonl").write_text('{"broken": \n', encoding="utf-8")
    assert main(["validate", str(d)]) == EXIT_FINDINGS
    assert "V001" in capsys.readouterr().out


def test_cli_list_checks(capsys):
    assert main(["validate", ".", "--list-checks"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "V040" in out and "V050" in out


def test_every_emitted_check_code_is_in_the_catalog():
    """A code missing from CHECK_CODES is invisible to --list-checks and makes
    `--skip <code>` fail as 'unknown'. Keeps the two in sync."""
    import re
    from pathlib import Path as P

    from qaai.dataset_studio.validate import CHECK_CODES

    src = P("qaai/dataset_studio/validate.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'(?:col\.add\(\s*|code=)"(V\d{3})"', src))
    assert emitted, "no check codes found - did the emit pattern change?"
    assert emitted <= set(CHECK_CODES), (
        f"emitted but undocumented: {sorted(emitted - set(CHECK_CODES))}"
    )
