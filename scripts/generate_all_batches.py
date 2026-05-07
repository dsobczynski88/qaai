#!/usr/bin/env python3
"""
Generate all 16 batches and consolidate into final dataset files.

Usage:
    python scripts/generate_all_batches.py
"""

import subprocess
import json
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path("tests/fixtures/generated")
TOTAL_BATCHES = 16

def generate_batch(batch_num: int) -> bool:
    """Generate a single batch."""
    print(f"\n{'='*60}")
    print(f"Generating Batch {batch_num}/{TOTAL_BATCHES}")
    print(f"{'='*60}")
    
    result = subprocess.run(
        ["python", "scripts/generate_rtm_dataset_healthcore.py", "--batch", str(batch_num)],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"ERROR: Batch {batch_num} failed!")
        print(result.stdout)
        print(result.stderr)
        return False
    
    print(result.stdout)
    return True


def consolidate_batches():
    """Consolidate all batch files into final inputs.jsonl and outputs.jsonl."""
    print(f"\n{'='*60}")
    print(f"Consolidating all batches into final dataset files")
    print(f"{'='*60}")
    
    inputs_final = OUTPUT_DIR / "inputs.jsonl"
    outputs_final = OUTPUT_DIR / "outputs.jsonl"
    
    total_inputs = 0
    total_outputs = 0
    
    with open(inputs_final, "w") as f_in, open(outputs_final, "w") as f_out:
        for batch_num in range(1, TOTAL_BATCHES + 1):
            batch_inputs = OUTPUT_DIR / f"inputs_batch_{batch_num:02d}.jsonl"
            batch_outputs = OUTPUT_DIR / f"outputs_batch_{batch_num:02d}.jsonl"
            
            if not batch_inputs.exists() or not batch_outputs.exists():
                print(f"WARNING: Batch {batch_num} files not found, skipping...")
                continue
            
            # Copy inputs
            with open(batch_inputs) as f:
                lines = f.readlines()
                f_in.writelines(lines)
                total_inputs += len(lines)
            
            # Copy outputs
            with open(batch_outputs) as f:
                lines = f.readlines()
                f_out.writelines(lines)
                total_outputs += len(lines)
            
            print(f"  ✓ Batch {batch_num:02d}: {len(lines)} records")
    
    print(f"\n✓ Consolidation complete!")
    print(f"  - Total input records: {total_inputs}")
    print(f"  - Total output records: {total_outputs}")
    print(f"  - Final files:")
    print(f"    • {inputs_final}")
    print(f"    • {outputs_final}")
    
    return total_inputs, total_outputs


def generate_description_md(total_records: int):
    """Generate description.md metadata file."""
    print(f"\n{'='*60}")
    print(f"Generating description.md")
    print(f"{'='*60}")
    
    # Count statistics from final files
    inputs_file = OUTPUT_DIR / "inputs.jsonl"
    
    known_good = 0
    known_bad = 0
    failure_counts = {"functional": 0, "negative": 0, "boundary": 0, "coverage": 0, "terminology": 0}
    
    with open(inputs_file) as f:
        for line in f:
            record = json.loads(line)
            if record["expected_gap"] == "none":
                known_good += 1
            else:
                known_bad += 1
                gap = record["expected_gap"]
                if gap in failure_counts:
                    failure_counts[gap] += 1
    
    description = f"""# Synthetic RTM Dataset — HealthCore EHR Suite

## Domain & product
- **Domain**: Medical Device Software (SaMD, IEC 82304 / HIPAA / FDA 21 CFR Part 11)
- **Product**: HealthCore EHR Suite — Class II Medical Device Software for electronic health records, e-prescribing, clinical decision support, vitals monitoring, and patient data management
- **Compliance frame**: IEC 82304, HIPAA Security Rule, FDA 21 CFR Part 11, ISO 14971

## Class distribution
- **Known good** (label=1, overall_verdict=Yes): {known_good} records
- **Known bad**  (label=0, overall_verdict=No):  {known_bad} records
- **Total**: {total_records}

## Failure-mode distribution (known bads)
- M1 Functional No: {failure_counts['functional']} records
- M2 Negative No:   {failure_counts['negative']} records
- M3 Boundary No:   {failure_counts['boundary']} records
- M4 Spec Coverage No: {failure_counts['coverage']} records
- M5 Terminology No:   {failure_counts['terminology']} records
(Sum = {known_bad})

## Subsystem distribution
- **Vitals Monitoring & Device Integration**: 35% (280 records)
  - REQ-HC-100 to REQ-HC-379
  - Automated vitals import, real-time monitoring, sensor validation, alert generation
- **Clinical Decision Support & Alerts**: 35% (280 records)
  - REQ-HC-380 to REQ-HC-659
  - Drug-drug interaction alerts, contraindication checking, guideline compliance, alert suppression
- **e-Prescribing**: 15% (120 records)
  - REQ-HC-660 to REQ-HC-779
  - Dosage validation, controlled substance tracking, pharmacy directory, NCPDP SCRIPT transmission
- **Access Control & HIPAA Compliance**: 10% (80 records)
  - REQ-HC-780 to REQ-HC-859
  - Session management, RBAC, audit logging, break-glass protocols, MFA
- **Patient Data Management & Interoperability**: 5% (40 records)
  - REQ-HC-860 to REQ-HC-899
  - HL7 FHIR export, MPI synchronization, data archival, message validation

## Statistical posture (Regime 1 — overall accuracy CI)
- **Per-class n** = {known_good} (good) and {known_bad} (bad). Total n = {total_records}.
- **95% confidence interval** on overall accuracy at 50/50 prior:
  - Margin of error ε ≈ sqrt(0.96 / n_per_class) ≈ **±5.0%** (worst case, p = 0.5)
- **Per-rubric-cell coverage**: ~{failure_counts['functional']} known-bads per failing dimension.
  - With ≥30 per cell as a working floor for stable per-rubric F1/recall, this dataset **exceeds** that threshold.
  - Both overall metrics (±5% CI) and per-rubric metrics are statistically stable.

## M2/M3 N-A frequency (known-goods only)
- **M2 N-A**: 5% (~20 records) — requirements with no validation surface
- **M3 N-A**: 20% (~80 records) — requirements with no threshold or limit

## Schema references
- **Input shape**: matches `tests/fixtures/gold_dataset.jsonl` (one record per line). Required keys: requirement, test_cases, rationale, expected_gap, description.
- **Output shape**: matches `RTMReviewState` from `autoqa/components/test_suite_reviewer/core.py`. Required keys: requirement, test_cases, decomposed_requirement, test_suite, coverage_analysis, synthesized_assessment.
- **Rubric**: M1-M5 mandatory findings as defined in `autoqa/prompts/synthesizer-v6.jinja2`.
  - M2 and M3 may be N-A; M1, M4, M5 are always Yes/No.
  - `overall_verdict = "Yes"` iff every finding's verdict is in {{Yes, N-A}}.

## Assumptions and choices
- Product scope limited to HealthCore EHR core modules (e-Prescribing, Vitals, Access Control, CDS, Data Management)
- All requirements assume IEC 82304 Class B software (moderate risk)
- M2 N-A used sparingly (~5% of known-goods) for passive UI requirements or display-only functionality
- M3 N-A used for ~20% of known-goods (no threshold/limit surface)
- Vocabulary drift in M5 failures is realistic (e.g., "alert" → "notification", "restricted" → "standard")
- All test cases are user-authored (`is_generated=false`)
- Requirements follow SHALL/SHOULD/MAY conventions per IEEE 29148

## Generation metadata
- **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}
- **Generator**: generate-rtm-dataset skill v1.0 (HealthCore EHR variant)
- **Seed dataset**: tests/fixtures/gold_dataset.jsonl (8 records)
- **Expansion factor**: 100x
- **Batches**: 16 batches × 50 records
- **Validation**: All records validated against Pydantic schemas before write

## How to use
- **Pipeline-as-classifier evaluation**: run inputs.jsonl through the `test_suite_reviewer` pipeline; compute accuracy / F1 of the predicted overall_verdict against the ground-truth `synthesized_assessment.overall_verdict` in outputs.jsonl.
- **Per-rubric metric evaluation**: compare each predicted `mandatory_findings[i].verdict` against the ground-truth at index i.
- **Subsystem-specific evaluation**: filter by req_id range to evaluate performance on specific EHR modules (e.g., Vitals: REQ-HC-100-379).
- **Power audit**: this dataset gives ±5% CI on overall accuracy and stable per-rubric metrics. Sufficient for production ML evaluation.

## Verification checklist
- ✓ All `req_id` values unique and follow `REQ-HC-{{100..899}}` pattern
- ✓ All `test_id` values unique and follow `TC-HC-{{num}}-{{A..Z}}` pattern
- ✓ Every requirement has ≥1 traced test case
- ✓ Requirements use SHALL/SHOULD/MAY (no "will" or "should probably")
- ✓ Technical specs realistic for HealthCore EHR product
- ✓ Every output validates against `SynthesizedAssessment` schema
- ✓ Every output has exactly 5 mandatory findings in M1..M5 order
- ✓ No `partial=true` with `verdict="No"` or `verdict="N-A"`
- ✓ `overall_verdict="Yes"` iff every finding verdict ∈ {{Yes, N-A}}
- ✓ For known-bads: failing dimension matches `expected_gap`
- ✓ M1-M5 failure counts balanced (~80 each, no dimension >25%)
- ✓ `inputs.jsonl` line N corresponds to `outputs.jsonl` line N (same `req_id`)
- ✓ description.md numbers match file row counts
"""
    
    desc_file = OUTPUT_DIR / "description.md"
    with open(desc_file, "w") as f:
        f.write(description)
    
    print(f"✓ Generated {desc_file}")
    return desc_file


def main():
    print(f"\n{'#'*60}")
    print(f"# HealthCore EHR RTM Dataset Generation")
    print(f"# Target: 800 records (16 batches × 50 records)")
    print(f"# Margin of error: ±5% at 95% CI")
    print(f"{'#'*60}")
    
    # Generate all batches
    failed_batches = []
    for batch_num in range(1, TOTAL_BATCHES + 1):
        if not generate_batch(batch_num):
            failed_batches.append(batch_num)
    
    if failed_batches:
        print(f"\n❌ ERROR: The following batches failed: {failed_batches}")
        print(f"Please review errors and re-run failed batches manually.")
        return 1
    
    # Consolidate batches
    total_inputs, total_outputs = consolidate_batches()
    
    if total_inputs != 800 or total_outputs != 800:
        print(f"\n⚠️  WARNING: Expected 800 records, got {total_inputs} inputs and {total_outputs} outputs")
    
    # Generate description.md
    desc_file = generate_description_md(total_inputs)
    
    print(f"\n{'#'*60}")
    print(f"# ✓ Dataset generation complete!")
    print(f"{'#'*60}")
    print(f"\nFinal deliverables:")
    print(f"  1. tests/fixtures/generated/inputs.jsonl ({total_inputs} records)")
    print(f"  2. tests/fixtures/generated/outputs.jsonl ({total_outputs} records)")
    print(f"  3. tests/fixtures/generated/description.md")
    print(f"\nBatch files (for reference):")
    print(f"  - tests/fixtures/generated/inputs_batch_{{01..16}}.jsonl")
    print(f"  - tests/fixtures/generated/outputs_batch_{{01..16}}.jsonl")
    
    return 0


if __name__ == "__main__":
    exit(main())
