"""Turn a completed review run into a reviewable answer-key dataset.

A run — ``logs/run-<ts>/`` from the API, or ``predictions/<ts>/`` from
``evaluate_with_mlflow.py --mode run`` — leaves behind the graph's inputs and its full
final state, plus a read-only HTML report. This module converts that pair into the
three row-aligned files the eval harness scores
(``actual_inputs.jsonl`` / ``actual_outputs.jsonl`` / ``actual_labels.jsonl``), so the
run can be opened in the Dataset Studio editor, corrected by a human, and saved as
ground truth.

The labels it writes are the model's *own* answers, not ground truth. That is the
point: the reviewer's job is to disagree where the model is wrong, and every such
disagreement is recorded in ``edits.log``. A set that comes out of here unreviewed is
an oracle self-test, not an answer key — ``description.md`` says so in the file.

⚠ **Alignment.** ``qaai.api.services._run_batch_review`` writes every input up front but
appends an output only for items whose graph run did not raise, so ``outputs.jsonl`` can
be *shorter* than ``inputs.jsonl`` and the two are not positionally aligned — while
positional alignment across the three files is the eval dataset's core invariant
(``qaai/eval/datasets.py``). This module therefore emits **one dataset row per output
row** and projects that row's input out of the output state itself, which carries the
input keys. Items with no output are reported and recorded in ``source.json``, never
silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from qaai.dataset_studio.editlog import EditRecord, append_edits, now_stamp
from qaai.dataset_studio.editor import build_rows
from qaai.dataset_studio.registry import (
    DATASET_TYPES,
    DatasetTypeInfo,
    assessment_key,
    dataset_type_for,
    infer_dataset_type,
    load_type_spec,
)
from qaai.dataset_studio.scaffold import (
    DESCRIPTION_NAME,
    EDITS_LOG_HEADER,
    EDITS_LOG_NAME,
    new_dataset_dir,
)
from qaai.dataset_studio.writer import write_dataset_atomic
from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    load_jsonl,
    outputs_to_labels,
)
from qaai.eval.spec import EvalSpec, get_path

__all__ = [
    "SOURCE_NAME",
    "RUN_FILE_PAIRS",
    "IngestError",
    "IngestResult",
    "find_run_files",
    "detect_dataset_type",
    "project_input",
    "ingest_run",
    "write_ingested",
]

#: Provenance sidecar, modelled on ``predictions/<ts>/run_metadata.json``.
SOURCE_NAME = "source.json"

#: (inputs, outputs) basenames to probe, in order. The first pair whose *outputs*
#: file exists wins, so a folder holding several conventions resolves predictably.
RUN_FILE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("inputs.jsonl", "outputs.jsonl"),                    # logs/run-<ts>/
    ("predicted_inputs.jsonl", "predicted_outputs.jsonl"),  # predictions/<ts>/
    ("actual_inputs.jsonl", "actual_outputs.jsonl"),      # an existing dataset
)


class IngestError(Exception):
    """A run folder that cannot be turned into a dataset (CLI-friendly message)."""


# ── discovery ───────────────────────────────────────────────────────────────

def find_run_files(
    run_dir: Union[str, Path],
    *,
    inputs: Optional[Union[str, Path]] = None,
    outputs: Optional[Union[str, Path]] = None,
) -> Tuple[Optional[Path], Path]:
    """Locate ``(inputs_path, outputs_path)`` in a run folder.

    Explicit paths win. The inputs file is optional — it is used only to report which
    items produced no output — but the outputs file is what a dataset is made of.
    """
    if outputs is not None:
        out = Path(outputs)
        if not out.is_file():
            raise IngestError(f"--outputs is not a file: {out}")
        inp = Path(inputs) if inputs else None
        if inp is not None and not inp.is_file():
            raise IngestError(f"--inputs is not a file: {inp}")
        return inp, out

    d = Path(run_dir)
    if not d.is_dir():
        raise IngestError(f"not a directory: {d}")

    for in_name, out_name in RUN_FILE_PAIRS:
        out = d / out_name
        if out.is_file():
            explicit = Path(inputs) if inputs else None
            found_in = explicit if explicit else (d / in_name)
            return (found_in if found_in.is_file() else None), out

    expected = ", ".join(o for _, o in RUN_FILE_PAIRS)
    raise IngestError(f"{d} contains none of: {expected}")


def detect_dataset_type(rows: Sequence[Any]) -> Optional[str]:
    """Infer the reviewer type from output state rows, or return None.

    Discriminates on which assessment key the state carries — ``synthesized_assessment``
    (RTM), ``aggregated_assessment`` (test case), ``hazard_assessment`` — read off each
    type's own eval spec rather than hard-coded here, so a spec that relocates its
    verdict stays detectable. Scans until a row matches, since a soft-failed leading row
    may carry no assessment at all.
    """
    keys: List[Tuple[str, str]] = []
    for name, info in DATASET_TYPES.items():
        try:
            keys.append((assessment_key(load_type_spec(info)), name))
        except Exception:  # a spec that will not load cannot claim a run
            continue

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, name in keys:
            if row.get(key) is not None:
                return name
    return None


# ── projection ──────────────────────────────────────────────────────────────

def project_input(state: Any, spec: EvalSpec) -> Dict[str, Any]:
    """Build one ``actual_inputs`` row from a graph output state.

    ``spec.input`` maps a logical state key to its dotted path in an inputs row; a graph
    state carries those same keys, so reading them back out reproduces the row the graph
    was invoked with. Keys the state does not carry are omitted rather than written as
    ``None``, which keeps optional fields (``design_docs``) absent instead of explicitly
    null.

    This is the first live consumer of ``spec.input`` — the run/score path builds its
    inputs through the hard-coded ``qaai.eval.runners`` builders instead.
    """
    row: Dict[str, Any] = {}
    for state_key, row_path in (spec.input or {}).items():
        value = get_path(state, state_key)
        if value is None:
            continue
        _set_path(row, row_path, value)
    return row


def _set_path(row: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = row
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _entity_id(row: Any, spec: EvalSpec) -> Optional[str]:
    """Best-effort human-readable id for a row, for the skipped-item report only.

    Nothing depends on this being right: it names items in a warning and in
    ``source.json``. Looks inside each ``spec.input`` value (and at the row itself, since
    a hazard ``inputs.jsonl`` line is the bare register row rather than the wrapped
    state) for the first ``id``/``*_id`` field.
    """
    candidates: List[Any] = [get_path(row, k) for k in (spec.input or {})]
    candidates.append(row)
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key, value in candidate.items():
                if (key == "id" or key.endswith("_id")) and isinstance(value, (str, int)):
                    return str(value)
    return None


# ── ingest ──────────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    """Rows ready to write, plus everything needed to explain where they came from."""

    dataset_type: str
    info: DatasetTypeInfo
    spec: EvalSpec
    rows: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_records(self) -> int:
        return len(self.rows)


def ingest_run(
    run_dir: Union[str, Path],
    *,
    dataset_type: Optional[str] = None,
    spec_path: Optional[Union[str, Path]] = None,
    inputs: Optional[Union[str, Path]] = None,
    outputs: Optional[Union[str, Path]] = None,
    reviewer: Optional[str] = None,
) -> IngestResult:
    """Read a run folder and build the aligned rows. Writes nothing."""
    inputs_path, outputs_path = find_run_files(run_dir, inputs=inputs, outputs=outputs)

    out_rows = load_jsonl(outputs_path)
    if not out_rows:
        raise IngestError(f"{outputs_path} has no rows")

    detected = detect_dataset_type(out_rows)
    if dataset_type is None:
        dataset_type = detected
        if dataset_type is None:
            raise IngestError(
                f"could not tell which reviewer produced {outputs_path} "
                f"(no known assessment key in any row); pass --type"
            )
    info = dataset_type_for(dataset_type)
    spec = load_type_spec(info, spec_path)

    key = assessment_key(spec)
    rows: List[Dict[str, Any]] = []
    labels = outputs_to_labels(spec, list(out_rows))
    n_no_assessment = 0
    for index, (state, label) in enumerate(zip(out_rows, labels)):
        if isinstance(state, dict) and state.get(key) is None:
            n_no_assessment += 1
        rows.append(
            {
                "index": index,
                "input": project_input(state, spec),
                "output": state,
                "label": dict(label),
            }
        )

    skipped = _skipped_items(inputs_path, out_rows, spec)

    provenance = {
        "dataset_type": dataset_type,
        "detected_type": detected,
        "spec": str(spec_path) if spec_path else f"eval/specs/{info.component}.yaml",
        "source_run_dir": str(Path(run_dir).resolve()) if Path(run_dir).exists() else None,
        "source_inputs_path": str(inputs_path) if inputs_path else None,
        "source_outputs_path": str(outputs_path),
        "source_inputs_sha256": _sha256(inputs_path) if inputs_path else None,
        "source_outputs_sha256": _sha256(outputs_path),
        "n_records": len(rows),
        "n_rows_without_assessment": n_no_assessment,
        "n_skipped": len(skipped),
        "skipped": skipped,
        "ingested_at": now_stamp(),
        "ingested_by": reviewer or _default_reviewer(),
        "labels_source": "model predictions (unreviewed) — corrected in Dataset Studio",
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
    }

    return IngestResult(
        dataset_type=dataset_type,
        info=info,
        spec=spec,
        rows=rows,
        skipped=skipped,
        provenance=provenance,
    )


def _skipped_items(
    inputs_path: Optional[Path], out_rows: Sequence[Any], spec: EvalSpec
) -> List[Dict[str, Any]]:
    """Items present in inputs.jsonl with no corresponding output row.

    Matched by entity id where both sides expose one; otherwise reported purely as a
    count difference, since a positional match would be exactly the wrong assumption —
    it is a mid-batch failure that shifts the two files out of step.
    """
    if inputs_path is None:
        return []
    in_rows = load_jsonl(inputs_path)
    if len(in_rows) <= len(out_rows):
        return []

    produced = {i for i in (_entity_id(r, spec) for r in out_rows) if i}
    skipped: List[Dict[str, Any]] = []
    for index, row in enumerate(in_rows):
        entity = _entity_id(row, spec)
        if entity is None or entity not in produced:
            skipped.append({"source_index": index, "entity_id": entity})
    return skipped


# ── write ───────────────────────────────────────────────────────────────────

def _load_or_empty(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file, or return ``[]`` when it is absent (a fresh-scaffold dir)."""
    return load_jsonl(path) if path.is_file() else []


def _append_ingested(
    result: IngestResult, target: Path, reviewer: Optional[str]
) -> Path:
    """Append ``result.rows`` onto an existing dataset, preserving its reviewed labels.

    The existing rows and their (already human-corrected) labels are kept; the new rows
    land after them and the whole set is re-indexed contiguously. Provenance and
    ``description.md`` are *not* clobbered — a human-reviewed ``description.md`` survives
    and each append drops its own timestamped ``source.<ts>.json`` sidecar — so the
    accumulating set never loses the history of what it already was.
    """
    if not target.is_dir():
        raise IngestError(f"--append target is not a directory: {target}")

    existing_type = infer_dataset_type(target)
    if existing_type is not None and existing_type != result.dataset_type:
        raise IngestError(
            f"cannot append {result.dataset_type} rows onto a {existing_type} dataset: {target}"
        )

    existing = build_rows(
        _load_or_empty(target / ACTUAL_INPUTS_NAME),
        _load_or_empty(target / ACTUAL_OUTPUTS_NAME),
        _load_or_empty(target / ACTUAL_LABELS_NAME),
    )
    combined: List[Dict[str, Any]] = existing + [dict(r) for r in result.rows]
    for i, row in enumerate(combined):
        row["index"] = i

    write_dataset_atomic(target, combined)

    # Provenance sidecar, timestamped so repeated appends never collide or clobber.
    # now_stamp() is ISO-8601 (colons, offset) — sanitize it for a cross-platform filename.
    stamp = result.provenance.get("ingested_at") or now_stamp()
    safe_stamp = "".join(c if (c.isalnum() or c in "-_") else "-" for c in stamp)
    sidecar = {
        **result.provenance,
        "appended_to": str(target.resolve()),
        "n_rows_appended": result.n_records,
        "n_total_after_append": len(combined),
    }
    (target / f"source.{safe_stamp}.json").write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not (target / DESCRIPTION_NAME).exists():
        (target / DESCRIPTION_NAME).write_text(_description(result, target), encoding="utf-8")

    log = target / EDITS_LOG_NAME
    if not log.exists():
        log.write_text(EDITS_LOG_HEADER, encoding="utf-8")
    append_edits(
        target,
        [
            EditRecord(
                action="ingest",
                by=reviewer or result.provenance.get("ingested_by") or "",
                note=(
                    f"append rows={result.n_records} total={len(combined)} "
                    f"type={result.dataset_type} "
                    f"from={result.provenance.get('source_outputs_path')}"
                ),
            )
        ],
    )
    return target


def write_ingested(
    result: IngestResult,
    *,
    out_dir: Optional[Union[str, Path]] = None,
    append_to: Optional[Union[str, Path]] = None,
    base_dir: Union[str, Path] = "eval/datasets",
    reviewer: Optional[str] = None,
) -> Path:
    """Write the ingested rows into a dataset directory.

    ``append_to`` adds the rows onto an existing dataset (keeping its reviewed labels);
    ``out_dir`` writes to that exact directory (overwriting all three files); with
    neither, a fresh timestamped directory is created. ``append_to`` and ``out_dir``
    are mutually exclusive.
    """
    if append_to is not None:
        if out_dir is not None:
            raise IngestError("pass either append_to or out_dir, not both")
        return _append_ingested(result, Path(append_to), reviewer)

    if out_dir is not None:
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = new_dataset_dir(result.dataset_type, base_dir)

    write_dataset_atomic(target, result.rows)

    (target / SOURCE_NAME).write_text(
        json.dumps(result.provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (target / DESCRIPTION_NAME).write_text(_description(result, target), encoding="utf-8")

    log = target / EDITS_LOG_NAME
    if not log.exists():
        log.write_text(EDITS_LOG_HEADER, encoding="utf-8")
    append_edits(
        target,
        [
            EditRecord(
                action="ingest",
                by=reviewer or result.provenance.get("ingested_by") or "",
                note=(
                    f"rows={result.n_records} skipped={len(result.skipped)} "
                    f"type={result.dataset_type} "
                    f"from={result.provenance.get('source_outputs_path')}"
                ),
            )
        ],
    )
    return target


def _description(result: IngestResult, target: Path) -> str:
    p = result.provenance
    skipped = ""
    if result.skipped:
        listed = ", ".join(str(s.get("entity_id") or s["source_index"]) for s in result.skipped[:20])
        more = " ..." if len(result.skipped) > 20 else ""
        skipped = (
            f"\n> **{len(result.skipped)} input item(s) produced no output** and are absent "
            f"from this set: {listed}{more}\n"
        )
    return f"""# {result.info.label} dataset - {target.name} (ingested run)

<!-- Created by `python -m qaai.dataset_studio ingest`. -->

## Status: UNREVIEWED

The labels here are **the model's own answers**, copied from the run's output state.
They are a starting point for review, not ground truth. Scoring this set as-is measures
nothing — it will return accuracy 1.000 against the predictions it came from.

Open it, correct every cell the model got wrong, and record why:

```
python -m qaai.dataset_studio edit {target.as_posix()}
```

Every correction and every reviewer note lands in `{EDITS_LOG_NAME}`.

**The governing rule, carried over from the committed pilot's `description.md`:**
a row earns `Yes` only if a competent reviewer reading it would agree, and a known-bad
row must carry a **real deficiency visible in the text**. An 800-row predecessor was
discarded for breaking this rule; it scored **kappa 0.000** because its labels were not
grounded in its content.

## Provenance
- Source run: `{p.get('source_run_dir')}`
- Outputs: `{p.get('source_outputs_path')}` (sha256 `{(p.get('source_outputs_sha256') or '')[:12]}`)
- Inputs: `{p.get('source_inputs_path')}`
- Reviewer type: `{result.dataset_type}` (detected: `{p.get('detected_type')}`)
- Spec: `{p.get('spec')}`
- Ingested: {p.get('ingested_at')} by {p.get('ingested_by')}
- Commit: `{p.get('git_sha')}`{' (dirty)' if p.get('git_dirty') else ''}
- Full detail: `{SOURCE_NAME}`

## Records
- Rows: {result.n_records}
- Rows whose run produced no assessment: {p.get('n_rows_without_assessment')}
- Input items with no output row: {len(result.skipped)}
{skipped}
## Class distribution
TODO - fill in after review (the pre-review counts are the model's, not the truth).

## Schema references
- Eval spec (authoritative rubric): `{p.get('spec')}`
- Live models: `qaai/agents/**/core.py`
"""


# ── small helpers ───────────────────────────────────────────────────────────

def _sha256(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _default_reviewer() -> str:
    import getpass

    try:
        return getpass.getuser()
    except Exception:
        return "unknown"
