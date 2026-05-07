#!/usr/bin/env python3
"""Generate batches 2-16 sequentially."""

import subprocess
import sys

for batch_num in range(2, 17):
    print(f"\n{'='*60}")
    print(f"Generating Batch {batch_num}/16")
    print(f"{'='*60}\n")
    
    result = subprocess.run(
        ["python", "scripts/generate_rtm_dataset_healthcore.py", "--batch", str(batch_num)],
        capture_output=False
    )
    
    if result.returncode != 0:
        print(f"\n❌ ERROR: Batch {batch_num} failed!")
        sys.exit(1)
    
    print(f"\n✓ Batch {batch_num} complete!")

print(f"\n{'='*60}")
print(f"✓ All batches 2-16 generated successfully!")
print(f"{'='*60}")
