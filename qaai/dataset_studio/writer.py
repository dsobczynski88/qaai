"""Atomic, all-or-nothing writes of a dataset's three JSONL files.

Positional alignment across ``actual_inputs`` / ``actual_outputs`` / ``actual_labels``
is the dataset's core invariant (see :mod:`qaai.eval.datasets`). A naive
write-three-files loop breaks that invariant on any mid-write failure, leaving a set
whose row counts disagree. So every file is serialized and staged first, and only
then are the three swapped into place.

Byte convention is inherited from :func:`qaai.eval.datasets.write_jsonl`:
``json.dumps(row, ensure_ascii=False)`` per row, ``\\n``-joined, single trailing
newline. ``tests/unit/dataset_studio/test_writer.py`` pins the two byte-for-byte so
the studio and the eval harness can never disagree about what a dataset file is.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
)

__all__ = ["serialize_jsonl", "write_jsonl_atomic", "write_dataset_atomic", "ROW_FILES"]

#: Payload key -> filename, in the order they are staged.
ROW_FILES: Dict[str, str] = {
    "input": ACTUAL_INPUTS_NAME,
    "output": ACTUAL_OUTPUTS_NAME,
    "label": ACTUAL_LABELS_NAME,
}


def serialize_jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
    """Exactly the byte convention of :func:`qaai.eval.datasets.write_jsonl`."""
    if not rows:
        return ""
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def _stage(path: Path, text: str) -> Path:
    """Write ``text`` to a sibling temp file and fsync it. Same directory keeps the
    subsequent ``os.replace`` on one volume, where it is atomic on NTFS and POSIX.

    Newline translation is left at the platform default, exactly as
    ``Path.write_text`` leaves it in :func:`qaai.eval.datasets.write_jsonl`. Forcing
    ``\\n`` here would be tidier in the abstract but would rewrite every line of a
    dataset the first time the editor touched it on Windows, turning a one-cell
    correction into a whole-file diff.
    """
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    return tmp


def write_jsonl_atomic(path: Union[str, Path], rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write one JSONL file atomically (stage + fsync + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _stage(path, serialize_jsonl(rows))
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def write_dataset_atomic(
    dataset_dir: Union[str, Path],
    rows: Sequence[Mapping[str, Any]],
    *,
    files: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """Write all three JSONL files, all-or-nothing.

    ``rows`` is the editor payload: a list of ``{"index", "input", "output", "label"}``
    records, already checked for contiguity by the caller. Every row is serialized and
    staged before any file is swapped, so a serialization error leaves the dataset
    completely untouched.

    A crash *between* the individual ``os.replace`` calls can still leave one file
    swapped and another not — an unavoidable filesystem limit without a journal. That
    residue is exactly what ``V002`` (row-count alignment) detects on the next open,
    which is why that check is an error rather than a warning.

    Returns the filenames written, in order.
    """
    d = Path(dataset_dir)
    d.mkdir(parents=True, exist_ok=True)
    files = files or ROW_FILES

    staged: List[tuple[Path, Path]] = []
    try:
        for key, name in files.items():
            payload = [dict(r.get(key) or {}) for r in rows]
            target = d / name
            staged.append((_stage(target, serialize_jsonl(payload)), target))
        for tmp, target in staged:
            os.replace(tmp, target)
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise

    return list(files.values())
