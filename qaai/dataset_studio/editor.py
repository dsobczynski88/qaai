"""Render the single-file dataset editor HTML.

Reuses :func:`qaai.viewer._loader.load_template`'s asset-inlining, with the editor
layout and JS bundle instead of the reviewer feedback ones. The result is one
self-contained page: no CDN, no build step, and it renders identically whether it is
served by :mod:`qaai.dataset_studio.server` or dumped to a file with ``--dump-html``.

Two payloads make the page type-agnostic:

``INPUT_SCHEMA``
    ``input_row_model(info, spec).model_json_schema()`` — the JSON Schema of the
    projected row model. The left pane's form is generated from it, so the fields a
    reviewer can edit are exactly the fields the graph state declares.

``CONFIG``
    Everything the output pane needs, derived from the ``EvalSpec``: rubric codes,
    which are advisory, which may be N-A, where the verdict lives. No rubric literal
    is written into the JS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from qaai.dataset_studio.registry import (
    DatasetTypeInfo,
    input_row_model,
)
from qaai.dataset_studio.rules import NA_COUNTS_AS_PASS, na_allowed_for
from qaai.eval.spec import EvalSpec
from qaai.viewer._loader import load_template

__all__ = ["EDITOR_TEMPLATE", "build_config", "build_rows", "build_editor_html"]

EDITOR_TEMPLATE = load_template(
    "dataset_editor",
    "Dataset Studio",
    layout="layout_editor.html",
    common_js=("dom.js", "editor.js"),
)


def _json_blob(value: Any) -> str:
    """Serialize for embedding in a ``<script>`` block.

    The ``</`` escape prevents a value containing ``</script>`` from closing the tag
    early — the same guard :func:`qaai.viewer.generator._render` applies.
    """
    return json.dumps(value, ensure_ascii=False, default=str).replace("</", "<\\/")


def _escape_html(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def build_config(
    info: DatasetTypeInfo,
    spec: EvalSpec,
    dataset_dir: Union[str, Path],
    *,
    save_url: str = "",
    token: str = "",
    reviewer: str = "",
    read_only: bool = False,
) -> Dict[str, Any]:
    """The ``CONFIG`` blob: every rubric fact the page needs, read off the spec."""
    rub = spec.output.rubric
    return {
        "dataset_type": info.name,
        "dataset_label": info.label,
        "dataset_dir": str(dataset_dir),
        "verdict_key": spec.labels.verdict_key,
        "verdict_path": spec.output.verdict_path,
        "rubric_list_path": rub.list_path if rub else "",
        "code_field": rub.code_field if rub else "code",
        "verdict_field": rub.verdict_field if rub else "verdict",
        "codes": list(rub.codes) if rub else [],
        "mandatory_codes": list(spec.mandatory_codes),
        "advisory_codes": list(spec.scoring.advisory_codes),
        "na_allowed": sorted(na_allowed_for(info.name)),
        "na_counts_as_pass": NA_COUNTS_AS_PASS.get(info.name, False),
        "labels": {
            "positive": spec.scoring.positive_label,
            "negative": spec.scoring.negative_label,
            "na": spec.scoring.na_label,
        },
        "save_url": save_url,
        "token": token,
        "reviewer": reviewer,
        "read_only": bool(read_only),
    }


def build_rows(
    inputs: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Zip the three row-aligned files into one record per page.

    Rows are joined positionally — the dataset's core invariant — and a short file
    still yields a record, so a misaligned dataset opens in the editor (where it can
    be fixed) rather than failing to load.
    """
    n = max(len(inputs), len(outputs), len(labels))
    return [
        {
            "index": i,
            "input": dict(inputs[i]) if i < len(inputs) else {},
            "output": dict(outputs[i]) if i < len(outputs) else {},
            "label": dict(labels[i]) if i < len(labels) else {},
        }
        for i in range(n)
    ]


def build_editor_html(
    *,
    info: DatasetTypeInfo,
    spec: EvalSpec,
    dataset_dir: Union[str, Path],
    rows: Sequence[Mapping[str, Any]],
    save_url: str = "",
    token: str = "",
    reviewer: str = "",
    read_only: bool = False,
    template: Optional[str] = None,
) -> str:
    """Render the editor for one dataset."""
    config = build_config(
        info, spec, dataset_dir,
        save_url=save_url, token=token, reviewer=reviewer, read_only=read_only,
    )
    # by_alias=False is load-bearing, not a preference. HazardRowFromExcel declares an
    # Excel column header as the alias of every field ("SHA ID Number" for hazard_id,
    # …), and Pydantic's schema uses validation aliases by default. Keying the form on
    # aliases would make every edit write `hazard["SHA ID Number"]` into a row that
    # already stores `hazard_id`, silently producing two keys for one field. Field
    # names are what the rows actually use, and populate_by_name accepts them.
    schema = input_row_model(info, spec).model_json_schema(by_alias=False)
    label = f"{info.label} - {Path(dataset_dir).name}"

    return (
        (template or EDITOR_TEMPLATE)
        .replace("{{DATA}}", _json_blob(list(rows)))
        .replace("{{CONFIG}}", _json_blob(config))
        .replace("{{INPUT_SCHEMA}}", _json_blob(schema))
        .replace("{{SOURCE}}", _escape_html(str(dataset_dir)))
        .replace("{{TITLE}}", _escape_html(f"Dataset Studio - {label}"))
        .replace("{{RUN_KEY}}", _escape_html(Path(dataset_dir).name))
    )
