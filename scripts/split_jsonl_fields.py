#!/usr/bin/env python3
"""
Split a full JSONL dataset into separate JSONL files for inputs, labels, and outputs.

Default field groups:
- inputs  : requirement, test_cases
- labels  : decomposed_requirement, coverage_analysis, synthesized_assessment
- outputs : decomposed_requirement, coverage_analysis, synthesized_assessment

Why labels and outputs are identical by default:
For supervised training/evaluation pipelines, the expected model outputs are often the
same objects as the ground-truth labels. If your desired outputs differ, update
OUTPUT_FIELDS below or pass a config file in a future enhancement.

Usage:
    python split_jsonl_fields.py input.jsonl \
        --inputs-out inputs.jsonl \
        --labels-out labels.jsonl \
        --outputs-out outputs.jsonl

Optional:
    --strict              Fail if any required field is missing.
    --pretty              Pretty-print JSON lines (larger files; usually not needed).
    --encoding utf-8      Override file encoding.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


# -----------------------------
# Default field groups
# -----------------------------
INPUT_FIELDS = [
    "requirement",
    "test_cases",
]

LABEL_FIELDS = [
    "decomposed_requirement",
    "coverage_analysis",
    "synthesized_assessment",
]

# By default, outputs == labels (common for supervised datasets).
OUTPUT_FIELDS = [
    "decomposed_requirement",
    "coverage_analysis",
    "synthesized_assessment",
]


# -----------------------------
# Core helpers
# -----------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a JSONL file into inputs/labels/outputs JSONL files by retaining selected fields."
    )
    parser.add_argument("input_jsonl", type=Path, help="Path to the source JSONL file")
    parser.add_argument("--inputs-out", type=Path, default=Path("inputs.jsonl"), help="Output path for inputs JSONL")
    parser.add_argument("--labels-out", type=Path, default=Path("labels.jsonl"), help="Output path for labels JSONL")
    parser.add_argument("--outputs-out", type=Path, default=Path("outputs.jsonl"), help="Output path for outputs JSONL")
    parser.add_argument("--strict", action="store_true", help="Fail if a required field is missing from any record")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON instead of compact JSONL formatting")
    parser.add_argument("--encoding", default="utf-8", help="Text encoding for input/output files (default: utf-8)")
    return parser.parse_args()


def extract_fields(record: Dict[str, Any], fields: Iterable[str], strict: bool, line_no: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    missing: List[str] = []

    for field in fields:
        if field in record:
            out[field] = record[field]
        else:
            missing.append(field)

    if missing and strict:
        raise KeyError(f"Line {line_no}: missing required field(s): {missing}")

    return out


def iter_jsonl(path: Path, encoding: str) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding=encoding) as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                # Skip blank lines safely
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_no}: {e}") from e
            if not isinstance(record, dict):
                raise ValueError(f"Line {line_no}: expected a JSON object, got {type(record).__name__}")
            yield line_no, record


def json_dumps(obj: Dict[str, Any], pretty: bool) -> str:
    if pretty:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(
    source_path: Path,
    out_path: Path,
    fields: List[str],
    strict: bool,
    pretty: bool,
    encoding: str,
) -> int:
    count = 0
    with out_path.open("w", encoding=encoding) as out_f:
        for line_no, record in iter_jsonl(source_path, encoding):
            reduced = extract_fields(record, fields, strict=strict, line_no=line_no)
            out_f.write(json_dumps(reduced, pretty=pretty))
            out_f.write("\n")
            count += 1
    return count


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    args = parse_args()

    if not args.input_jsonl.exists():
        print(f"ERROR: Input file not found: {args.input_jsonl}", file=sys.stderr)
        return 1

    for p in (args.inputs_out, args.labels_out, args.outputs_out):
        ensure_parent_dir(p)

    try:
        in_count = write_jsonl(
            source_path=args.input_jsonl,
            out_path=args.inputs_out,
            fields=INPUT_FIELDS,
            strict=args.strict,
            pretty=args.pretty,
            encoding=args.encoding,
        )
        label_count = write_jsonl(
            source_path=args.input_jsonl,
            out_path=args.labels_out,
            fields=LABEL_FIELDS,
            strict=args.strict,
            pretty=args.pretty,
            encoding=args.encoding,
        )
        output_count = write_jsonl(
            source_path=args.input_jsonl,
            out_path=args.outputs_out,
            fields=OUTPUT_FIELDS,
            strict=args.strict,
            pretty=args.pretty,
            encoding=args.encoding,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print("Done.")
    print(f"  Source   : {args.input_jsonl}")
    print(f"  Inputs   : {args.inputs_out} ({in_count} records)")
    print(f"  Labels   : {args.labels_out} ({label_count} records)")
    print(f"  Outputs  : {args.outputs_out} ({output_count} records)")
    print()
    print("Field groups used:")
    print(f"  INPUT_FIELDS  = {INPUT_FIELDS}")
    print(f"  LABEL_FIELDS  = {LABEL_FIELDS}")
    print(f"  OUTPUT_FIELDS = {OUTPUT_FIELDS}")

    if LABEL_FIELDS == OUTPUT_FIELDS:
        print()
        print("Note: LABEL_FIELDS and OUTPUT_FIELDS are identical by default.")
        print("If you want outputs to differ, edit OUTPUT_FIELDS near the top of the script.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
