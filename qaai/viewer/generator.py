"""Build and write HTML viewer files from RTM or test-case pipeline outputs.jsonl."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Iterable, Optional, Union

from qaai.viewer.template import HTML_TEMPLATE
from qaai.viewer.template_eval_compare import EVAL_COMPARE_TEMPLATE
from qaai.viewer.template_hazard_review import HZ_HTML_TEMPLATE
from qaai.viewer.template_test_case import TC_HTML_TEMPLATE

PathLike = Union[str, pathlib.Path]


def _render(
    records: Iterable[dict],
    source_label: str,
    run_key: str,
    template: str,
    title_prefix: str,
    review_type: str,
    log_entries: Optional[list] = None,
) -> str:
    data_json = json.dumps(list(records), ensure_ascii=False)
    data_json = data_json.replace("</", "<\\/")
    log_json = json.dumps(list(log_entries or []), ensure_ascii=False)
    log_json = log_json.replace("</", "<\\/")
    return (
        template
        .replace("{{DATA}}", data_json)
        .replace("{{LOG}}", log_json)
        .replace("{{SOURCE}}", _escape_html(source_label))
        .replace("{{TITLE}}", _escape_html(f"{title_prefix} — {source_label}"))
        .replace("{{RUN_KEY}}", _escape_html(run_key))
        # review_type labels the exported feedback file:
        # feedback_{review_type}_{run_key}.json
        .replace("{{REVIEW_TYPE}}", review_type)
    )


# Per-review-type rendering spec: (template, title_prefix, feedback_type, default_filename).
# The three reviewers differ only by these four values, so the build/write logic below is
# shared and keyed by review_type. feedback_type labels the exported feedback file and gives
# each viewer a distinct localStorage namespace so their ratings never collide for one run.
_VIEWER_SPECS: dict[str, tuple[str, str, str, str]] = {
    "test_suite": (HTML_TEMPLATE, "Batch output viewer", "test_suite", "viewer.html"),
    "test_case": (TC_HTML_TEMPLATE, "Test case output viewer", "test_case", "viewer_tc.html"),
    "hazard": (HZ_HTML_TEMPLATE, "Hazard reviewer output viewer", "hazard", "viewer_hz.html"),
    "eval_compare": (EVAL_COMPARE_TEMPLATE, "Actual vs predicted", "eval_compare", "compare.html"),
}


def _build(
    review_type: str, records: Iterable[dict], source_label: str, run_key: str,
    log_entries: Optional[list] = None,
) -> str:
    template, title_prefix, feedback_type, _ = _VIEWER_SPECS[review_type]
    return _render(records, source_label, run_key, template, title_prefix, feedback_type, log_entries)


def build_viewer(
    records: Iterable[dict], source_label: str, run_key: str,
    log_entries: Optional[list] = None,
) -> str:
    """Render the single-file HTML viewer for RTM (test_suite_reviewer) records.

    ``source_label`` appears in the title/header (usually the JSONL path).
    ``run_key`` becomes part of the localStorage key that stores reviewer feedback,
    so the same run's ratings persist across re-opens of the same viewer.
    ``log_entries`` (problem notes from the run) populate the "View log" button.
    """
    return _build("test_suite", records, source_label, run_key, log_entries)


def build_viewer_tc(
    records: Iterable[dict], source_label: str, run_key: str,
    log_entries: Optional[list] = None,
) -> str:
    """Render the single-file HTML viewer for test-case-reviewer records.

    Same contract as :func:`build_viewer` but renders TCReviewState records
    using the test-case template.
    """
    return _build("test_case", records, source_label, run_key, log_entries)


def build_viewer_hz(
    records: Iterable[dict], source_label: str, run_key: str,
    log_entries: Optional[list] = None,
) -> str:
    """Render the single-file HTML viewer for hazard-risk-reviewer records.

    Same contract as :func:`build_viewer` but renders HazardReviewState
    records using the hazard template.
    """
    return _build("hazard", records, source_label, run_key, log_entries)


def build_viewer_compare(
    records: Iterable[dict], source_label: str, run_key: str,
    log_entries: Optional[list] = None,
) -> str:
    """Render the single-file actual-vs-predicted diff viewer.

    Unlike the three reviewer viewers, ``records`` here are the merged comparison
    rows built in memory by :mod:`qaai.eval.compare` (each carries both the actual
    answer-key values and the run's predicted values), not a plain ``outputs.jsonl``.
    ``run_key`` namespaces the localStorage feedback (usually the predictions
    timestamp).
    """
    return _build("eval_compare", records, source_label, run_key, log_entries)


def _read_records(jsonl_path: PathLike) -> tuple[pathlib.Path, list[dict]]:
    src = pathlib.Path(jsonl_path)
    if not src.exists():
        raise FileNotFoundError(src)
    records: list[dict] = []
    for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{src}:{i}: invalid JSON ({e})") from e
    return src, records


def _write(
    review_type: str,
    jsonl_path: PathLike,
    output_path: Optional[PathLike] = None,
    log_entries: Optional[list] = None,
) -> Optional[pathlib.Path]:
    """Read ``jsonl_path``, render the ``review_type`` viewer, write to ``output_path``.

    Default output is ``<jsonl_dir>/<default_filename>`` (per :data:`_VIEWER_SPECS`),
    so a single run directory can hold the RTM, test-case, and hazard viewers side by
    side. Returns the output path on success; returns ``None`` when the JSONL is empty
    (no viewer is written). Raises ``FileNotFoundError`` if the input does not exist.
    ``log_entries`` are the run's problem notes shown by the "View log" button.
    """
    default_filename = _VIEWER_SPECS[review_type][3]
    src, records = _read_records(jsonl_path)
    if not records:
        return None
    out = pathlib.Path(output_path) if output_path else src.parent / default_filename
    run_key = src.parent.name or src.stem
    out.write_text(_build(review_type, records, str(src), run_key, log_entries), encoding="utf-8")
    return out


def write_viewer(
    jsonl_path: PathLike, output_path: Optional[PathLike] = None,
    log_entries: Optional[list] = None,
) -> Optional[pathlib.Path]:
    """Render the RTM viewer (default ``<jsonl_dir>/viewer.html``). See :func:`_write`."""
    return _write("test_suite", jsonl_path, output_path, log_entries)


def write_viewer_tc(
    jsonl_path: PathLike, output_path: Optional[PathLike] = None,
    log_entries: Optional[list] = None,
) -> Optional[pathlib.Path]:
    """Render the test-case viewer (default ``<jsonl_dir>/viewer_tc.html``). See :func:`_write`."""
    return _write("test_case", jsonl_path, output_path, log_entries)


def write_viewer_hz(
    jsonl_path: PathLike, output_path: Optional[PathLike] = None,
    log_entries: Optional[list] = None,
) -> Optional[pathlib.Path]:
    """Render the hazard viewer (default ``<jsonl_dir>/viewer_hz.html``). See :func:`_write`."""
    return _write("hazard", jsonl_path, output_path, log_entries)


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl_path", help="Path to outputs.jsonl (one state record per line)")
    ap.add_argument("-o", "--output", default=None,
                    help="Output HTML path. Default: <jsonl_dir>/viewer.html (rtm), viewer_tc.html (tc), or viewer_hz.html (hz)")
    ap.add_argument("--type", choices=("rtm", "tc", "hz"), default="rtm",
                    help="Which viewer to render: 'rtm' (test_suite_reviewer, default), 'tc' (test_case_reviewer), or 'hz' (hazard_risk_reviewer)")
    args = ap.parse_args(argv)

    writer = {"rtm": write_viewer, "tc": write_viewer_tc, "hz": write_viewer_hz}[args.type]
    try:
        out = writer(args.jsonl_path, args.output)
    except FileNotFoundError as e:
        print(f"error: {e} does not exist", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    if out is None:
        print(f"error: {args.jsonl_path} has no records", file=sys.stderr)
        return 4

    src = pathlib.Path(args.jsonl_path)
    n = sum(1 for line in src.read_text(encoding="utf-8").splitlines() if line.strip())
    print(f"wrote {out}  ({n} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
