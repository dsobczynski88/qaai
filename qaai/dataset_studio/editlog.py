"""The append-only ``edits.log`` written beside a dataset's JSONL files.

Format: tab-separated, exactly **7 fields**, one line per action::

    <iso8601-ct>\\t<action>\\trow=<%04d|----->\\t<file>\\t<path>\\t<before> -> <after>\\tby=<user>

Rendered::

    # qaai dataset-studio edit log - append-only, never rewritten
    2026-07-19T10:35:02.114-05:00	edit	row=0007	actual_labels.jsonl	M3	"Yes" -> "No"	by=dsobc
    2026-07-19T10:35:20.006-05:00	accept	row=0008	-	-	-	by=dsobc
    2026-07-19T10:36:41.250-05:00	save	row=-----	actual_inputs.jsonl,...	-	rows=20 edits=12	by=dsobc

Design notes:

* Values are rendered with ``json.dumps``, so an embedded tab, newline, or quote is
  escaped and a record is **always** exactly one line with exactly 7
  tab-separated fields — parseable by ``line.split("\\t")`` with no quoting rules.
* ``row`` is zero-padded so the log sorts and greps cleanly; ``-----`` marks a
  dataset-level action.
* ``path`` uses the same dotted + ``[i]`` rendering as
  :attr:`qaai.dataset_studio.validate.Finding.path`, so a validation finding greps
  straight out of this log.
* Timestamps use ``US_CENTRAL`` — the timezone of the dataset folder names and of
  ``logs/run-<ts>/`` — so a dataset, its edits, and the run that scored it share one
  clock.
* Long values truncate at :data:`MAX_VALUE_CHARS`; the full value is on disk in the
  JSONL, so the log is an index of *what changed*, not a second copy of the data.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

from pydantic import BaseModel

from qaai.core.logging_config import US_CENTRAL

__all__ = [
    "MAX_VALUE_CHARS",
    "MAX_NOTE_CHARS",
    "ACTIONS",
    "read_edits",
    "EditRecord",
    "now_stamp",
    "format_line",
    "parse_line",
    "append_edits",
]

MAX_VALUE_CHARS = 200

#: ``note`` payloads are prose written by a reviewer, not a diff, so they get a far
#: larger budget — clipping someone's justification at 200 chars mid-sentence destroys
#: exactly the evidence the log exists to hold. Still bounded, so one pasted document
#: cannot make a single unreadable line.
MAX_NOTE_CHARS = 4000

TRUNCATION_MARK = "…"
ACTIONS = (
    "ingest",     # a run folder was converted into this dataset
    "edit",
    "accept",
    "feedback",   # a reviewer's written justification for one record
    "save",
    "save-as",
    "force-save",
    "revert",
)

_ARROW = " -> "
_NONE = "-"
_NO_ROW = "-----"
_FIELD_COUNT = 7


class EditRecord(BaseModel):
    """One line of the edit log."""

    action: str
    at: str = ""
    index: Optional[int] = None
    file: Optional[str] = None
    path: Optional[str] = None
    before: Any = None
    after: Any = None
    by: str = ""
    #: Set for non-``edit`` actions whose payload is a summary rather than a diff
    #: (e.g. ``rows=20 edits=12 validation=pass``).
    note: Optional[str] = None


def now_stamp() -> str:
    """Millisecond ISO-8601 timestamp in US/Central."""
    return datetime.now(tz=US_CENTRAL).isoformat(timespec="milliseconds")


def _dump(value: Any, max_chars: int) -> str:
    """Serialize a value to JSON, truncating **before** serializing.

    Truncating the serialized text instead would emit malformed JSON — and worse,
    malformed in a way that can silently re-parse as a *different* valid value: cutting
    ``"xxx…"`` mid-string lets the next quote in the line close it, so ``before``
    swallows the ``" -> "`` separator and the diff is misread. Truncating the value
    keeps the emitted text well-formed, so :func:`_split_diff` is never ambiguous.
    """
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    # Represent the over-long value as a truncated string, marked as elided.
    body = value if isinstance(value, str) else text
    return json.dumps(body[: max(1, max_chars - 1)] + TRUNCATION_MARK, ensure_ascii=False)


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # truncated, or not JSON to begin with


def format_line(rec: EditRecord, *, max_value_chars: int = MAX_VALUE_CHARS) -> str:
    """Render one record as a single tab-separated line (no trailing newline)."""
    if rec.note is not None:
        value = (
            rec.note
            if len(rec.note) <= MAX_NOTE_CHARS
            else rec.note[: MAX_NOTE_CHARS - 1] + TRUNCATION_MARK
        )
    elif rec.before is None and rec.after is None:
        value = _NONE
    else:
        value = (
            _dump(rec.before, max_value_chars)
            + _ARROW
            + _dump(rec.after, max_value_chars)
        )

    fields = [
        rec.at or now_stamp(),
        rec.action,
        f"row={rec.index:04d}" if rec.index is not None else f"row={_NO_ROW}",
        rec.file or _NONE,
        rec.path or _NONE,
        value,
        f"by={rec.by or _NONE}",
    ]
    # json.dumps has already escaped any tab/newline inside the values; the remaining
    # risk is a caller passing a raw tab as a path or filename.
    return "\t".join(f.replace("\t", " ").replace("\n", " ") for f in fields)


def parse_line(line: str) -> Optional[EditRecord]:
    """Parse a log line back into an :class:`EditRecord`, or None for a comment/blank.

    Round-trips :func:`format_line` for every action. Truncated values come back as
    their truncated text, which is why this is an audit-reading aid rather than a way
    to reconstruct data.
    """
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) != _FIELD_COUNT:
        return None

    at, action, row, file, path, value, by = parts
    index: Optional[int] = None
    if row.startswith("row=") and row[4:] != _NO_ROW:
        try:
            index = int(row[4:])
        except ValueError:
            index = None

    before = after = None
    note = None
    if value != _NONE:
        split = _split_diff(value)
        if split is None:
            note = value
        else:
            before, after = split

    return EditRecord(
        at=at, action=action, index=index,
        file=None if file == _NONE else file,
        path=None if path == _NONE else path,
        before=before, after=after, note=note,
        by="" if by == "by=-" else by[3:] if by.startswith("by=") else by,
    )


def _split_diff(value: str) -> Optional[tuple[Any, Any]]:
    """Split a ``<before> -> <after>`` value into its two decoded halves.

    Splitting on the first ``" -> "`` is wrong: the separator is ordinary text and can
    appear *inside* either value (requirement prose says "A -> B" all the time). Both
    halves are JSON, so the decoder itself is asked where the first value ends —
    unambiguous by construction.

    Returns None when the value is not a diff (a summary note), or when either half is
    unparseable because it was truncated.
    """
    decoder = json.JSONDecoder()
    try:
        before, end = decoder.raw_decode(value)
        rest = value[end:]
        if not rest.startswith(_ARROW):
            return None
        after, end2 = decoder.raw_decode(rest[len(_ARROW):])
    except json.JSONDecodeError:
        return None
    if rest[len(_ARROW) + end2:].strip():
        return None  # trailing junk: not a diff we wrote
    return before, after


def append_edits(
    dataset_dir: Union[str, Path],
    records: Sequence[EditRecord],
    *,
    log_name: str = "edits.log",
    max_value_chars: int = MAX_VALUE_CHARS,
) -> int:
    """Append records to ``<dataset_dir>/edits.log``, creating it with its header.

    Called only **after** the JSONL write has landed, so the log can never claim a
    write that did not happen. Returns the number of lines appended.
    """
    from qaai.dataset_studio.scaffold import EDITS_LOG_HEADER

    if not records:
        return 0
    path = Path(dataset_dir) / log_name
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0

    with path.open("a", encoding="utf-8") as fh:
        if new_file:
            fh.write(EDITS_LOG_HEADER)
        for rec in records:
            fh.write(format_line(rec, max_value_chars=max_value_chars) + "\n")
        fh.flush()
    return len(records)


def read_edits(
    dataset_dir: Union[str, Path], *, log_name: str = "edits.log"
) -> List[EditRecord]:
    """Parse an existing edit log (skipping comments). Missing file -> []."""
    path = Path(dataset_dir) / log_name
    if not path.exists():
        return []
    out: List[EditRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = parse_line(line)
        if rec is not None:
            out.append(rec)
    return out
