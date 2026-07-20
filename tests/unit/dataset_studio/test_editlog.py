"""Edit log: exactly 7 fields per line, round-trippable, append-only."""

import pytest

from qaai.dataset_studio.editlog import (
    ACTIONS,
    MAX_VALUE_CHARS,
    EditRecord,
    append_edits,
    format_line,
    parse_line,
    read_edits,
)

pytestmark = pytest.mark.unit


def test_format_line_has_exactly_seven_fields():
    line = format_line(EditRecord(
        action="edit", at="2026-07-19T10:35:02.114-05:00", index=7,
        file="actual_labels.jsonl", path="M3", before="Yes", after="No", by="dsobc",
    ))
    assert line.split("\t") == [
        "2026-07-19T10:35:02.114-05:00", "edit", "row=0007",
        "actual_labels.jsonl", "M3", '"Yes" -> "No"', "by=dsobc",
    ]


def test_row_is_zero_padded_and_dataset_level_is_dashes():
    assert "row=0007" in format_line(EditRecord(action="edit", index=7))
    assert "row=-----" in format_line(EditRecord(action="save"))


@pytest.mark.parametrize("action", ACTIONS)
def test_round_trip_for_every_action(action):
    rec = EditRecord(
        action=action, at="2026-07-19T10:35:02.114-05:00", index=3,
        file="actual_outputs.jsonl",
        path="synthesized_assessment.mandatory_findings[2].verdict",
        before="Yes", after="No", by="dsobc",
    )
    back = parse_line(format_line(rec))
    assert back is not None
    assert (back.action, back.index, back.file, back.path) == (
        action, 3, "actual_outputs.jsonl",
        "synthesized_assessment.mandatory_findings[2].verdict",
    )
    assert (back.before, back.after, back.by) == ("Yes", "No", "dsobc")


def test_nasty_values_stay_on_one_line_with_seven_fields():
    """A tab, newline, quote, and a literal ' -> ' inside the data must not break parsing."""
    nasty = 'a\tb\nc "quoted" and -> arrow'
    line = format_line(EditRecord(
        action="edit", index=0, file="actual_inputs.jsonl",
        path="requirement.text", before=nasty, after="clean", by="dsobc",
    ))
    assert "\n" not in line
    assert len(line.split("\t")) == 7

    back = parse_line(line)
    assert back.before == nasty
    assert back.after == "clean"


@pytest.mark.parametrize("before,after", [
    ("state A -> state B", "clean"),
    ("clean", "state A -> state B"),
    ("a -> b", "c -> d"),
])
def test_arrow_inside_the_data_does_not_confuse_the_split(before, after):
    """The ' -> ' separator is ordinary prose and appears inside real requirement text.

    Splitting on the first occurrence would mis-parse these; the JSON decoder is asked
    where the first value actually ends instead.
    """
    back = parse_line(format_line(EditRecord(
        action="edit", index=0, file="actual_inputs.jsonl", path="requirement.text",
        before=before, after=after, by="d",
    )))
    assert (back.before, back.after) == (before, after)


def test_structured_values_round_trip():
    back = parse_line(format_line(EditRecord(
        action="edit", index=0, path="test_cases",
        before=["TC-1"], after=["TC-1", "TC-2"], by="d",
    )))
    assert back.before == ["TC-1"] and back.after == ["TC-1", "TC-2"]


def test_long_values_truncate_but_still_parse_correctly():
    """Truncation must not corrupt the diff.

    Regression guard: truncating the *serialized* text instead of the value cut a
    string mid-quote, letting the next quote on the line close it — so `before`
    silently absorbed the ' -> ' separator and the after-value was lost.
    """
    rec = EditRecord(action="edit", index=0, before="x" * 500, after="y", by="d")
    line = format_line(rec)
    assert "…" in line
    assert len(line.split("\t")) == 7

    parsed = parse_line(line)
    assert parsed is not None
    assert parsed.after == "y", "the after-value must survive truncation of before"
    assert isinstance(parsed.before, str)
    assert parsed.before.startswith("xxx") and parsed.before.endswith("…")
    assert parsed.note is None


def test_long_structured_values_stay_valid_json():
    rec = EditRecord(action="edit", index=0, before=[f"TC-{i}" for i in range(200)],
                     after="short", by="d")
    parsed = parse_line(format_line(rec))
    assert parsed.after == "short"
    assert "…" in parsed.before


def test_custom_truncation_limit():
    line = format_line(EditRecord(action="edit", before="x" * 50, after="y"), max_value_chars=10)
    assert "…" in line
    assert MAX_VALUE_CHARS == 200


def test_note_actions_render_a_summary_not_a_diff():
    line = format_line(EditRecord(
        action="save", file="actual_inputs.jsonl,actual_outputs.jsonl",
        note="rows=20 edits=12 validation=pass", by="dsobc",
    ))
    fields = line.split("\t")
    assert fields[5] == "rows=20 edits=12 validation=pass"
    assert parse_line(line).note == "rows=20 edits=12 validation=pass"


def test_accept_action_records_review_coverage():
    """An accept has no diff — the log records that the row was reviewed at all."""
    line = format_line(EditRecord(action="accept", index=8, by="dsobc"))
    assert line.split("\t")[5] == "-"
    back = parse_line(line)
    assert back.action == "accept" and back.before is None and back.after is None


def test_parse_line_ignores_comments_and_blanks():
    assert parse_line("# qaai dataset-studio edit log") is None
    assert parse_line("") is None
    assert parse_line("   ") is None
    assert parse_line("not\ta\tvalid\tline") is None


# ── append behavior ─────────────────────────────────────────────────────────

def test_append_creates_the_header_once_then_appends(tmp_path):
    n = append_edits(tmp_path, [EditRecord(action="edit", index=0, before=1, after=2, by="d")])
    assert n == 1
    text = (tmp_path / "edits.log").read_text(encoding="utf-8")
    assert text.startswith("# qaai dataset-studio edit log")

    append_edits(tmp_path, [EditRecord(action="accept", index=1, by="d")])
    text2 = (tmp_path / "edits.log").read_text(encoding="utf-8")
    assert text2.count("# qaai dataset-studio edit log") == 1
    assert text2.startswith(text)  # append-only: earlier content is untouched
    assert len(read_edits(tmp_path)) == 2


def test_append_empty_list_is_a_noop(tmp_path):
    assert append_edits(tmp_path, []) == 0
    assert not (tmp_path / "edits.log").exists()


def test_read_edits_on_missing_log(tmp_path):
    assert read_edits(tmp_path) == []


def test_appending_to_a_scaffolded_log_keeps_one_header(tmp_path):
    from qaai.dataset_studio.scaffold import scaffold_dataset

    d = scaffold_dataset("test_suite", base_dir=tmp_path)
    append_edits(d, [EditRecord(action="edit", index=0, before="Yes", after="No", by="d")])
    text = (d / "edits.log").read_text(encoding="utf-8")
    assert text.count("# qaai dataset-studio edit log") == 1
    assert len(read_edits(d)) == 1
