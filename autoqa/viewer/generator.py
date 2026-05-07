"""Build and write HTML viewer files from RTM or test-case pipeline outputs.jsonl."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Iterable, Optional, Union

from autoqa.viewer.template import HTML_TEMPLATE
from autoqa.viewer.template_hazard_review import HZ_HTML_TEMPLATE
from autoqa.viewer.template_test_case import TC_HTML_TEMPLATE

PathLike = Union[str, pathlib.Path]


def _render(
    records: Iterable[dict],
    source_label: str,
    run_key: str,
    template: str,
    title_prefix: str,
) -> str:
    data_json = json.dumps(list(records), ensure_ascii=False)
    data_json = data_json.replace("</", "<\\/")
    return (
        template
        .replace("{{DATA}}", data_json)
        .replace("{{SOURCE}}", _escape_html(source_label))
        .replace("{{TITLE}}", _escape_html(f"{title_prefix} — {source_label}"))
        .replace("{{RUN_KEY}}", _escape_html(run_key))
    )


def build_viewer(records: Iterable[dict], source_label: str, run_key: str) -> str:
    """Render the single-file HTML viewer for RTM (test_suite_reviewer) records.

    ``source_label`` appears in the title/header (usually the JSONL path).
    ``run_key`` becomes part of the localStorage key that stores reviewer feedback,
    so the same run's ratings persist across re-opens of the same viewer.
    """
    return _render(records, source_label, run_key, HTML_TEMPLATE, "Batch output viewer")


def build_viewer_tc(records: Iterable[dict], source_label: str, run_key: str) -> str:
    """Render the single-file HTML viewer for test-case-reviewer records.

    Same contract as :func:`build_viewer` but renders TCReviewState records
    using the test-case template. The localStorage key namespace is distinct
    so RTM feedback and test-case feedback never collide for the same run.
    """
    return _render(records, source_label, run_key, TC_HTML_TEMPLATE, "Test case output viewer")


def build_viewer_hz(records: Iterable[dict], source_label: str, run_key: str) -> str:
    """Render the single-file HTML viewer for hazard-risk-reviewer records.

    Same contract as :func:`build_viewer` but renders HazardReviewState
    records using the hazard template. The localStorage key namespace is
    distinct so RTM, test-case, and hazard feedback never collide for the
    same run.
    """
    return _render(records, source_label, run_key, HZ_HTML_TEMPLATE, "Hazard reviewer output viewer")


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


def write_viewer(
    jsonl_path: PathLike,
    output_path: Optional[PathLike] = None,
) -> Optional[pathlib.Path]:
    """Read ``jsonl_path``, render the RTM viewer, write to ``output_path``.

    Default output is ``<jsonl_dir>/viewer.html``. Returns the output path on
    success; returns ``None`` when the JSONL is empty (no viewer is written).
    Raises ``FileNotFoundError`` if the input path does not exist.
    """
    src, records = _read_records(jsonl_path)
    if not records:
        return None
    out = pathlib.Path(output_path) if output_path else src.parent / "viewer.html"
    run_key = src.parent.name or src.stem
    out.write_text(build_viewer(records, str(src), run_key), encoding="utf-8")
    return out


def write_viewer_tc(
    jsonl_path: PathLike,
    output_path: Optional[PathLike] = None,
) -> Optional[pathlib.Path]:
    """Read ``jsonl_path``, render the test-case viewer, write to ``output_path``.

    Default output is ``<jsonl_dir>/viewer_tc.html`` so a single run directory
    can hold both an RTM viewer and a test-case viewer side by side.
    """
    src, records = _read_records(jsonl_path)
    if not records:
        return None
    out = pathlib.Path(output_path) if output_path else src.parent / "viewer_tc.html"
    run_key = src.parent.name or src.stem
    out.write_text(build_viewer_tc(records, str(src), run_key), encoding="utf-8")
    return out


def write_viewer_hz(
    jsonl_path: PathLike,
    output_path: Optional[PathLike] = None,
) -> Optional[pathlib.Path]:
    """Read ``jsonl_path``, render the hazard viewer, write to ``output_path``.

    Default output is ``<jsonl_dir>/viewer_hz.html`` so a single run directory
    can hold RTM, test-case, and hazard viewers side by side.
    """
    src, records = _read_records(jsonl_path)
    if not records:
        return None
    out = pathlib.Path(output_path) if output_path else src.parent / "viewer_hz.html"
    run_key = src.parent.name or src.stem
    out.write_text(build_viewer_hz(records, str(src), run_key), encoding="utf-8")
    return out


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
