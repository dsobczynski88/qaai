#!/usr/bin/env python3
"""Monitor progress of batch generation."""

from pathlib import Path
import time

OUTPUT_DIR = Path("tests/fixtures/generated")
TOTAL_BATCHES = 16

def check_progress():
    """Check how many batches have been generated."""
    completed = 0
    for batch_num in range(1, TOTAL_BATCHES + 1):
        batch_file = OUTPUT_DIR / f"inputs_batch_{batch_num:02d}.jsonl"
        if batch_file.exists():
            completed += 1
    
    return completed

print("Monitoring batch generation progress...\n")

while True:
    completed = check_progress()
    progress_pct = (completed / TOTAL_BATCHES) * 100
    
    bar_length = 40
    filled = int(bar_length * completed / TOTAL_BATCHES)
    bar = '█' * filled + '░' * (bar_length - filled)
    
    print(f"\r[{bar}] {completed}/{TOTAL_BATCHES} batches ({progress_pct:.1f}%)", end='', flush=True)
    
    if completed == TOTAL_BATCHES:
        print("\n\n✓ All batches generated!")
        
        # Check if consolidation is done
        if (OUTPUT_DIR / "inputs.jsonl").exists():
            print("✓ Consolidation complete!")
            print("✓ Dataset generation finished!")
        else:
            print("⏳ Waiting for consolidation...")
        break
    
    time.sleep(2)
