"""Scaffolding: timestamped, append-only, and never clobbering an existing set."""

import re
from pathlib import Path

import pytest

from qaai.dataset_studio.cli import EXIT_MISSING, EXIT_OK, main
from qaai.dataset_studio.scaffold import (
    ANSWER_KEY_SUBDIR,
    DESCRIPTION_NAME,
    EDITS_LOG_NAME,
    new_dataset_dir,
    scaffold_dataset,
    timestamp,
)
from qaai.dataset_studio.validate import validate_dataset

pytestmark = pytest.mark.unit

TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
EXPECTED_FILES = {
    "actual_inputs.jsonl",
    "actual_outputs.jsonl",
    "actual_labels.jsonl",
    DESCRIPTION_NAME,
    EDITS_LOG_NAME,
}


def test_timestamp_matches_the_run_directory_convention():
    """Same shape as logs/run-<ts>/ and predictions/<ts>/."""
    assert TS_RE.fullmatch(timestamp())


@pytest.mark.parametrize("dtype", ["test_suite", "test_case", "hazard"])
def test_scaffold_creates_the_skeleton_under_the_type(tmp_path, dtype):
    out = scaffold_dataset(dtype, base_dir=tmp_path)
    assert out.parent.name == ANSWER_KEY_SUBDIR
    assert out.parent.parent.name == dtype
    assert out.parent.parent.parent == tmp_path
    assert TS_RE.fullmatch(out.name)
    assert {p.name for p in out.iterdir()} == EXPECTED_FILES


def test_two_scaffolds_in_one_second_get_distinct_directories(tmp_path):
    """Never clobber: a second scaffold must not hand back the first's directory.

    Without this, two generation runs inside the same second would silently
    overwrite each other's authored rows.
    """
    a = scaffold_dataset("hazard", base_dir=tmp_path)
    (a / "actual_labels.jsonl").write_text('{"Overall_Verdict": "Yes"}\n', encoding="utf-8")
    b = scaffold_dataset("hazard", base_dir=tmp_path)

    assert a != b
    assert a.exists() and b.exists()
    # The first set's content survived.
    assert (a / "actual_labels.jsonl").read_text(encoding="utf-8").strip()
    assert (b / "actual_labels.jsonl").read_text(encoding="utf-8") == ""


def test_edits_log_starts_with_the_header(tmp_path):
    out = scaffold_dataset("test_suite", base_dir=tmp_path)
    text = (out / EDITS_LOG_NAME).read_text(encoding="utf-8")
    assert text.startswith("# qaai dataset-studio edit log")


def test_description_stub_carries_the_grounding_rule(tmp_path):
    """The kappa-0.000 lesson is the most valuable thing in the committed dataset."""
    out = scaffold_dataset("test_suite", base_dir=tmp_path)
    text = (out / DESCRIPTION_NAME).read_text(encoding="utf-8")
    assert "kappa 0.000" in text
    assert "competent reviewer reading it would agree" in text
    assert "eval/specs/test_suite_reviewer.yaml" in text


def test_description_title_override(tmp_path):
    out = scaffold_dataset("hazard", base_dir=tmp_path, title="Infusion pump pilot")
    assert (out / DESCRIPTION_NAME).read_text(encoding="utf-8").startswith(
        "# Infusion pump pilot"
    )


def test_seed_from_copies_the_jsonl_but_not_the_provenance(tmp_path):
    src = scaffold_dataset("test_suite", base_dir=tmp_path)
    (src / "actual_labels.jsonl").write_text(
        '{"Overall_Verdict": "Yes", "M1": "Yes"}\n', encoding="utf-8"
    )
    (src / EDITS_LOG_NAME).write_text("# header\nsome earlier edit\n", encoding="utf-8")

    dst = scaffold_dataset("test_suite", base_dir=tmp_path, seed_from=src)
    assert dst != src
    assert (dst / "actual_labels.jsonl").read_text(encoding="utf-8") == (
        src / "actual_labels.jsonl"
    ).read_text(encoding="utf-8")
    # A branched set gets a fresh audit trail, not the parent's.
    assert "some earlier edit" not in (dst / EDITS_LOG_NAME).read_text(encoding="utf-8")
    assert f"Seeded from `{src}`" in (dst / DESCRIPTION_NAME).read_text(encoding="utf-8")


def test_seed_from_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        scaffold_dataset("test_suite", base_dir=tmp_path, seed_from=tmp_path / "nope")


def test_new_dataset_dir_rejects_an_unknown_type(tmp_path):
    with pytest.raises(KeyError):
        new_dataset_dir("nope", base_dir=tmp_path)


def test_scaffolded_dataset_validates_as_empty_not_broken(tmp_path):
    """An empty skeleton should report no *errors* — it is unfinished, not wrong."""
    out = scaffold_dataset("test_suite", base_dir=tmp_path)
    report = validate_dataset(out)
    assert report.n_errors == 0, report.to_text(0)


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_new_quiet_prints_only_the_path(tmp_path, capsys):
    assert main(["new", "--type", "hazard", "--base-dir", str(tmp_path), "--quiet"]) == EXIT_OK
    printed = capsys.readouterr().out.strip()
    assert (tmp_path / "hazard").exists()
    assert TS_RE.fullmatch(printed.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])


def test_cli_new_verbose_lists_the_files(tmp_path, capsys):
    assert main(["new", "--type", "test_case", "--base-dir", str(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "created" in out
    for name in EXPECTED_FILES:
        assert name in out


def test_cli_scaffold_alias(tmp_path):
    assert main(["scaffold", "--type", "test_suite", "--base-dir", str(tmp_path), "--quiet"]) == EXIT_OK


def test_sync_outputs_reproduces_the_committed_answer_key(tmp_path, capsys):
    """Deriving outputs from labels is what makes V050 pass by construction.

    Byte-identical to the committed pilot's actual_outputs.jsonl, which was itself
    produced by synthesize_outputs — so the generator skills can author only inputs +
    labels and never hand-write the outputs file.

    Must point at the hand-authored pilot revision specifically, not at any sibling
    under actual/: an ingested set's outputs are also synthesize_outputs-shaped, so a
    wrong path here would still pass while testing a different dataset.
    """
    import shutil

    committed = Path("eval/datasets/test_suite/actual/2026-07-17_12-01-00")
    d = tmp_path / "test_suite" / "2026-07-19_12-00-00"
    d.mkdir(parents=True)
    for name in ("actual_inputs.jsonl", "actual_labels.jsonl"):
        shutil.copyfile(committed / name, d / name)
    (d / "actual_outputs.jsonl").write_text("", encoding="utf-8")

    assert main(["sync-outputs", str(d)]) == EXIT_OK
    assert (d / "actual_outputs.jsonl").read_bytes() == (
        committed / "actual_outputs.jsonl"
    ).read_bytes()
    assert validate_dataset(d).n_errors == 0


def test_sync_outputs_refuses_to_clobber_without_force(tmp_path, capsys):
    d = scaffold_dataset("test_suite", base_dir=tmp_path)
    (d / "actual_labels.jsonl").write_text('{"Overall_Verdict": "Yes"}\n', encoding="utf-8")
    (d / "actual_outputs.jsonl").write_text('{"existing": true}\n', encoding="utf-8")

    assert main(["sync-outputs", str(d)]) != EXIT_OK
    assert "existing" in (d / "actual_outputs.jsonl").read_text(encoding="utf-8")
    assert main(["sync-outputs", str(d), "--force"]) == EXIT_OK
    assert "existing" not in (d / "actual_outputs.jsonl").read_text(encoding="utf-8")


def test_sync_outputs_missing_labels_exits_2(tmp_path):
    d = tmp_path / "test_suite" / "2026-07-19_12-00-00"
    d.mkdir(parents=True)
    assert main(["sync-outputs", str(d)]) == EXIT_MISSING


def test_cli_new_missing_seed_exits_2(tmp_path):
    assert main([
        "new", "--type", "test_suite", "--base-dir", str(tmp_path),
        "--from-dataset", str(tmp_path / "nope"),
    ]) == EXIT_MISSING
