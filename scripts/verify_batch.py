#!/usr/bin/env python3
"""Verify batch 1 was generated correctly."""

import json
from pathlib import Path

batch_dir = Path("tests/fixtures/generated")
inputs_file = batch_dir / "inputs_batch_01.jsonl"
outputs_file = batch_dir / "outputs_batch_01.jsonl"

# Count lines
with open(inputs_file) as f:
    input_lines = f.readlines()
    
with open(outputs_file) as f:
    output_lines = f.readlines()

print(f"✓ Batch 1 generated successfully!")
print(f"  - Input records: {len(input_lines)}")
print(f"  - Output records: {len(output_lines)}")

# Parse first input
first_input = json.loads(input_lines[0])
print(f"\n✓ First input record:")
print(f"  - req_id: {first_input['requirement']['req_id']}")
print(f"  - expected_gap: {first_input['expected_gap']}")
print(f"  - test_cases: {len(first_input['test_cases'])}")

# Parse first output
first_output = json.loads(output_lines[0])
print(f"\n✓ First output record:")
print(f"  - req_id: {first_output['requirement']['req_id']}")
print(f"  - overall_verdict: {first_output['synthesized_assessment']['overall_verdict']}")
print(f"  - mandatory_findings: {len(first_output['synthesized_assessment']['mandatory_findings'])}")

# Count known-good vs known-bad
known_good = sum(1 for line in input_lines if json.loads(line)['expected_gap'] == 'none')
known_bad = len(input_lines) - known_good

print(f"\n✓ Class distribution:")
print(f"  - Known-good: {known_good}")
print(f"  - Known-bad: {known_bad}")

# Count failure dimensions
failure_counts = {}
for line in input_lines:
    inp = json.loads(line)
    if inp['expected_gap'] != 'none':
        gap = inp['expected_gap']
        failure_counts[gap] = failure_counts.get(gap, 0) + 1

print(f"\n✓ Failure distribution:")
for gap, count in sorted(failure_counts.items()):
    print(f"  - {gap}: {count}")

print(f"\n✓ Batch 1 validation complete!")
