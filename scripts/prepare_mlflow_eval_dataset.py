#!/usr/bin/env python3
"""
Prepare MLflow evaluation dataset from batched JSONL files.

Reads all inputs_batch_*.jsonl and outputs_batch_*.jsonl files from
tests/fixtures/generated/, then produces three consolidated files in
tests/fixtures/mlflow_eval/:

1. eval_inputs.jsonl
   - Fields: requirement, test_cases
   - Purpose: Input to test_suite_reviewer LangGraph

2. eval_outputs.jsonl
   - Fields: decomposed_requirement, coverage_analysis, synthesized_assessment
   - Purpose: Full ground-truth state for detailed analysis

3. eval_outputs_labels.jsonl
   - Fields: Overall_Verdict, M1, M2, M3, M4, M5
   - Purpose: Flattened binary/ternary labels for classification metrics
   - Format: {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "No", "M3": "N-A", ...}

Usage:
    python scripts/prepare_mlflow_eval_dataset.py

Optional:
    --source-dir tests/fixtures/generated
    --output-dir tests/fixtures/mlflow_eval
    --strict              Fail if any required field is missing
    --encoding utf-8      Override file encoding
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


# -----------------------------
# Field groups
# -----------------------------
INPUT_FIELDS = ["requirement", "test_cases"]

OUTPUT_FIELDS = [
    "decomposed_requirement",
    "coverage_analysis",
    "synthesized_assessment",
]

LABEL_FIELDS = [
    "Overall_Verdict",  # Extracted from synthesized_assessment.overall_verdict
    "M1", "M2", "M3", "M4", "M5",  # Extracted from synthesized_assessment.mandatory_findings
]


# -----------------------------
# Core helpers
# -----------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare MLflow evaluation dataset from batched JSONL files."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("tests/fixtures/generated"),
        help="Directory containing inputs_batch_*.jsonl and outputs_batch_*.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures/mlflow_eval"),
        help="Output directory for eval_inputs.jsonl, eval_outputs.jsonl, eval_outputs_labels.jsonl",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if a required field is missing from any record",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Text encoding for input/output files (default: utf-8)",
    )
    return parser.parse_args()


def find_batch_files(source_dir: Path, pattern: str) -> List[Path]:
    """Find all batch files matching pattern, sorted numerically."""
    files = sorted(
        source_dir.glob(pattern),
        key=lambda p: int(p.stem.split("_")[-1])  # Extract batch number
    )
    if not files:
        raise FileNotFoundError(f"No files matching '{pattern}' in {source_dir}")
    return files


def iter_jsonl(path: Path, encoding: str) -> List[Dict[str, Any]]:
    """Load all records from a JSONL file."""
    records = []
    with path.open("r", encoding=encoding) as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name} line {line_no}: Invalid JSON: {e}") from e
            if not isinstance(record, dict):
                raise ValueError(
                    f"{path.name} line {line_no}: expected JSON object, got {type(record).__name__}"
                )
            records.append(record)
    return records


def extract_fields(
    record: Dict[str, Any],
    fields: List[str],
    strict: bool,
    source_file: str,
    record_idx: int,
) -> Dict[str, Any]:
    """Extract specified fields from a record."""
    out = {}
    missing = []

    for field in fields:
        if field in record:
            out[field] = record[field]
        else:
            missing.append(field)

    if missing and strict:
        raise KeyError(
            f"{source_file} record {record_idx}: missing required field(s): {missing}"
        )

    return out


def extract_labels(
    record: Dict[str, Any],
    strict: bool,
    source_file: str,
    record_idx: int,
) -> Dict[str, str]:
    """
    Extract flattened labels from synthesized_assessment.
    
    Returns:
        {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "No", "M3": "N-A", "M4": "Yes", "M5": "Yes"}
    """
    try:
        sa = record["synthesized_assessment"]
        overall_verdict = sa["overall_verdict"]
        mandatory_findings = sa["mandatory_findings"]
    except KeyError as e:
        if strict:
            raise KeyError(
                f"{source_file} record {record_idx}: missing synthesized_assessment field: {e}"
            ) from e
        # Return empty labels if not strict
        return {
            "Overall_Verdict": "Unknown",
            "M1": "Unknown",
            "M2": "Unknown",
            "M3": "Unknown",
            "M4": "Unknown",
            "M5": "Unknown",
        }

    # Extract M1-M5 verdicts
    labels = {"Overall_Verdict": overall_verdict}
    
    # Ensure mandatory_findings is a list of 5 items
    if len(mandatory_findings) != 5:
        if strict:
            raise ValueError(
                f"{source_file} record {record_idx}: expected 5 mandatory_findings, got {len(mandatory_findings)}"
            )
        # Pad with Unknown if not strict
        for i in range(1, 6):
            code = f"M{i}"
            if i - 1 < len(mandatory_findings):
                labels[code] = mandatory_findings[i - 1]["verdict"]
            else:
                labels[code] = "Unknown"
    else:
        for finding in mandatory_findings:
            code = finding["code"]
            verdict = finding["verdict"]
            labels[code] = verdict

    return labels


def write_jsonl(
    records: List[Dict[str, Any]],
    out_path: Path,
    encoding: str,
) -> int:
    """Write records to JSONL file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding=encoding) as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
    return len(records)


# -----------------------------
# Main processing
# -----------------------------
def process_batches(
    source_dir: Path,
    output_dir: Path,
    strict: bool,
    encoding: str,
) -> Tuple[int, int, int]:
    """
    Process all batch files and generate three output files.
    
    Returns:
        (inputs_count, outputs_count, labels_count)
    """
    # Find all batch files
    input_files = find_batch_files(source_dir, "inputs_batch_*.jsonl")
    output_files = find_batch_files(source_dir, "outputs_batch_*.jsonl")

    if len(input_files) != len(output_files):
        raise ValueError(
            f"Mismatch: {len(input_files)} input batches vs {len(output_files)} output batches"
        )

    print(f"Found {len(input_files)} batch pairs in {source_dir}")

    # Accumulate records across all batches
    all_inputs = []
    all_outputs = []
    all_labels = []

    for input_file, output_file in zip(input_files, output_files):
        print(f"  Processing {input_file.name} + {output_file.name}...")

        # Load batch
        input_records = iter_jsonl(input_file, encoding)
        output_records = iter_jsonl(output_file, encoding)

        if len(input_records) != len(output_records):
            raise ValueError(
                f"{input_file.name}: {len(input_records)} records, "
                f"{output_file.name}: {len(output_records)} records (mismatch)"
            )

        # Process each record in the batch
        for idx, (inp, out) in enumerate(zip(input_records, output_records)):
            # 1. Extract input fields
            input_reduced = extract_fields(
                inp, INPUT_FIELDS, strict, input_file.name, idx
            )
            all_inputs.append(input_reduced)

            # 2. Extract output fields
            output_reduced = extract_fields(
                out, OUTPUT_FIELDS, strict, output_file.name, idx
            )
            all_outputs.append(output_reduced)

            # 3. Extract flattened labels
            labels = extract_labels(out, strict, output_file.name, idx)
            all_labels.append(labels)

    # Write consolidated files
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs_path = output_dir / "eval_inputs.jsonl"
    outputs_path = output_dir / "eval_outputs.jsonl"
    labels_path = output_dir / "eval_outputs_labels.jsonl"

    inputs_count = write_jsonl(all_inputs, inputs_path, encoding)
    outputs_count = write_jsonl(all_outputs, outputs_path, encoding)
    labels_count = write_jsonl(all_labels, labels_path, encoding)

    print()
    print("✓ Consolidation complete:")
    print(f"  {inputs_path} ({inputs_count} records)")
    print(f"  {outputs_path} ({outputs_count} records)")
    print(f"  {labels_path} ({labels_count} records)")

    return inputs_count, outputs_count, labels_count


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    args = parse_args()

    if not args.source_dir.exists():
        print(f"ERROR: Source directory not found: {args.source_dir}", file=sys.stderr)
        return 1

    try:
        inputs_count, outputs_count, labels_count = process_batches(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            strict=args.strict,
            encoding=args.encoding,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2

    # Verification
    if inputs_count != outputs_count or inputs_count != labels_count:
        print(
            f"WARNING: Record count mismatch: "
            f"inputs={inputs_count}, outputs={outputs_count}, labels={labels_count}",
            file=sys.stderr,
        )
        return 3

    print()
    print("Field groups used:")
    print(f"  INPUT_FIELDS  = {INPUT_FIELDS}")
    print(f"  OUTPUT_FIELDS = {OUTPUT_FIELDS}")
    print(f"  LABEL_FIELDS  = {LABEL_FIELDS}")
    print()
    print("Next steps:")
    print("  1. Verify record counts match across all three files")
    print("  2. Spot-check a few records in each file")
    print("  3. Run MLflow evaluation harness with these files")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
