"""Create the timestamped folder a new dataset is written into.

Layout, mirroring the run-directory convention elsewhere in the repo::

    eval/datasets/<type>/actual/<YYYY-MM-DD_HH-MM-SS>/
        actual_inputs.jsonl
        actual_outputs.jsonl
        actual_labels.jsonl
        description.md
        edits.log

Append-only by construction: every scaffold call makes a new directory, so the
committed pilot at ``eval/datasets/test_suite/`` can never be clobbered by a
generation run.

The ``description.md`` stub is modelled on the committed
``eval/datasets/test_suite/actual/<ts>/description.md``, whose "Why this set exists"
section is
the most valuable thing in that dataset — it records that an earlier 800-row set
scored **kappa 0.000** because its labels were not grounded in its content. The stub
carries that governing rule forward so the next author reads it before generating.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from qaai.core.logging_config import US_CENTRAL
from qaai.dataset_studio.registry import dataset_type_for
from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
)

__all__ = [
    "ANSWER_KEY_SUBDIR",
    "EDITS_LOG_NAME",
    "DESCRIPTION_NAME",
    "timestamp",
    "new_dataset_dir",
    "scaffold_dataset",
]

EDITS_LOG_NAME = "edits.log"
DESCRIPTION_NAME = "description.md"

#: Groups every answer-key revision of a reviewer type under one parent, so the
#: human-curated sets sit beside — not tangled with — the ``predictions/`` tree.
ANSWER_KEY_SUBDIR = "actual"

JSONL_NAMES = (ACTUAL_INPUTS_NAME, ACTUAL_OUTPUTS_NAME, ACTUAL_LABELS_NAME)

EDITS_LOG_HEADER = (
    "# qaai dataset-studio edit log - appended by qaai.dataset_studio.server; "
    "append-only, never rewritten\n"
)


def timestamp() -> str:
    """``%Y-%m-%d_%H-%M-%S`` in US/Central.

    Same format and timezone as ``qaai.core.logging_config.create_timestamped_run_directory``
    (``logs/run-<ts>/``) and ``qaai.eval.datasets.new_predictions_dir``, so a dataset,
    its predictions, and the run log that produced them all sort and read alike.
    """
    return datetime.now(tz=US_CENTRAL).strftime("%Y-%m-%d_%H-%M-%S")


def new_dataset_dir(
    dataset_type: str,
    base_dir: Union[str, Path] = "eval/datasets",
    *,
    subdir: Optional[str] = ANSWER_KEY_SUBDIR,
) -> Path:
    """Create and return a fresh ``<base_dir>/<dataset_type>/<subdir>/<timestamp>/``.

    ``subdir`` defaults to ``"actual"``, which groups every answer-key revision of a
    reviewer type under one parent and keeps it clearly distinct from the ``predictions/``
    tree that a scoring run writes. Pass ``subdir=None`` for the flat
    ``<type>/<timestamp>/`` layout used before that segment existed.

    Unlike :func:`qaai.eval.datasets.new_predictions_dir`, which passes
    ``exist_ok=True``, this refuses to hand back a directory that already exists: two
    scaffolds within the same second would otherwise return the same path and the
    second would overwrite the first's authored rows. Predictions are regenerable, so
    that trade-off is fine there; a dataset is authored content and is not. On a
    collision the timestamp gains a ``-2``, ``-3``, ... suffix.
    """
    info = dataset_type_for(dataset_type)
    parent = Path(base_dir) / info.name
    if subdir:
        parent = parent / subdir
    ts = timestamp()
    for attempt in range(1, 101):
        d = parent / (ts if attempt == 1 else f"{ts}-{attempt}")
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:
            continue
    raise FileExistsError(f"could not allocate a fresh dataset directory under {parent}")


def scaffold_dataset(
    dataset_type: str,
    *,
    base_dir: Union[str, Path] = "eval/datasets",
    title: Optional[str] = None,
    seed_from: Optional[Union[str, Path]] = None,
) -> Path:
    """Create a new timestamped dataset folder with the five-file skeleton.

    ``seed_from`` copies the three JSONL files from an existing dataset directory so a
    reviewed set can be branched rather than regenerated. ``description.md`` and
    ``edits.log`` are never copied: the new set needs its own provenance and its own
    audit trail.
    """
    info = dataset_type_for(dataset_type)
    out = new_dataset_dir(dataset_type, base_dir)

    if seed_from is not None:
        src = Path(seed_from)
        if not src.is_dir():
            raise FileNotFoundError(f"--from-dataset is not a directory: {src}")
        found = False
        for name in JSONL_NAMES:
            if (src / name).exists():
                shutil.copyfile(src / name, out / name)
                found = True
            else:
                (out / name).write_text("", encoding="utf-8")
        if not found:
            raise FileNotFoundError(
                f"--from-dataset {src} contains none of {', '.join(JSONL_NAMES)}"
            )
    else:
        for name in JSONL_NAMES:
            (out / name).write_text("", encoding="utf-8")

    (out / EDITS_LOG_NAME).write_text(EDITS_LOG_HEADER, encoding="utf-8")
    (out / DESCRIPTION_NAME).write_text(
        _description_stub(info.name, info.label, info.component, out.name, title, seed_from),
        encoding="utf-8",
    )
    return out


def _description_stub(
    type_name: str,
    type_label: str,
    component: str,
    ts: str,
    title: Optional[str],
    seed_from: Optional[Union[str, Path]],
) -> str:
    heading = title or f"{type_label} dataset - {ts}"
    provenance = (
        f"- Seeded from `{seed_from}`\n" if seed_from else "- Generated from scratch\n"
    )
    return f"""# {heading}

<!-- Scaffolded by `python -m qaai.dataset_studio new --type {type_name}`.
     Replace every TODO before treating this set as evidence. -->

## Domain & product
- Domain: TODO (default: medical-device software - SiMD / SaMD, IEC 82304)
- Product: TODO
- Compliance frame: IEC 62304, ISO 14971, FDA 21 CFR 820.30

## Why this set exists
TODO - what question this dataset answers.

**The governing rule, carried over from the committed pilot's `description.md`:**
a row earns `Yes` only if a competent reviewer reading it would agree, and a
known-bad row must carry a **real deficiency visible in the text** - not merely a
missing row. An 800-row predecessor was discarded for breaking this rule; it scored
**kappa 0.000** because its labels were not grounded in its content.

## Class distribution
- Known good (Overall_Verdict = Yes): TODO
- Known bad  (Overall_Verdict = No):  TODO
- Total: TODO

## Failure-mode distribution (known bads)
| Cell | Count | Records |
|---|---|---|
| TODO | | |

## Statistical posture
- n = TODO. See `qaai/eval/sample_size.py`: 95% / +-0.05 needs 385 at p=0.5, 196 at p=0.85.
- One row per unique subject keeps the design effect at 1, so the i.i.d. interval is valid.

## Schema references
- Eval spec (authoritative rubric): `eval/specs/{component}.yaml`
- Live models: `qaai/agents/**/core.py`

## Provenance
{provenance}- Created: {ts} (US/Central)
- Edits since creation: see `{EDITS_LOG_NAME}`

## Verification gates before spending a run
Run `python -m qaai.dataset_studio validate <this dir>` - it enforces the mechanical
gates (row alignment, rubric completeness, verdict derivation, answer-key
self-agreement). It cannot judge grounding; that is what the editor
(`python -m qaai.dataset_studio edit <this dir>`) and a human reviewer are for.
"""
