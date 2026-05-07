#!/usr/bin/env python3
"""Quick verification script to test the MLflow evaluation setup.

This script performs sanity checks before running the full evaluation:
1. Verifies batch files exist
2. Checks consolidated files are valid
3. Validates label format
4. Tests a single record through the pipeline

Usage:
    python scripts/verify_eval_setup.py
"""
import json
import sys
from pathlib import Path


def check_batch_files():
    """Verify all 16 batch pairs exist."""
    print("=" * 60)
    print("1. Checking batch files...")
    print("=" * 60)
    
    source_dir = Path("tests/fixtures/generated")
    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}")
        return False
    
    input_files = sorted(source_dir.glob("inputs_batch_*.jsonl"))
    output_files = sorted(source_dir.glob("outputs_batch_*.jsonl"))
    
    print(f"Found {len(input_files)} input batch files")
    print(f"Found {len(output_files)} output batch files")
    
    if len(input_files) != 16:
        print(f"❌ Expected 16 input batch files, found {len(input_files)}")
        return False
    
    if len(output_files) != 16:
        print(f"❌ Expected 16 output batch files, found {len(output_files)}")
        return False
    
    print("✅ All batch files present")
    return True


def check_consolidated_files():
    """Verify consolidated files exist and have matching record counts."""
    print("\n" + "=" * 60)
    print("2. Checking consolidated files...")
    print("=" * 60)
    
    eval_dir = Path("tests/fixtures/mlflow_eval")
    
    if not eval_dir.exists():
        print(f"❌ Evaluation directory not found: {eval_dir}")
        print("   Run: uv run python scripts/prepare_mlflow_eval_dataset.py")
        return False
    
    inputs_path = eval_dir / "eval_inputs.jsonl"
    outputs_path = eval_dir / "eval_outputs.jsonl"
    labels_path = eval_dir / "eval_outputs_labels.jsonl"
    
    for path in [inputs_path, outputs_path, labels_path]:
        if not path.exists():
            print(f"❌ File not found: {path}")
            print("   Run: uv run python scripts/prepare_mlflow_eval_dataset.py")
            return False
    
    # Count records
    inputs_count = sum(1 for line in inputs_path.read_text().splitlines() if line.strip())
    outputs_count = sum(1 for line in outputs_path.read_text().splitlines() if line.strip())
    labels_count = sum(1 for line in labels_path.read_text().splitlines() if line.strip())
    
    print(f"eval_inputs.jsonl: {inputs_count} records")
    print(f"eval_outputs.jsonl: {outputs_count} records")
    print(f"eval_outputs_labels.jsonl: {labels_count} records")
    
    if inputs_count != outputs_count or inputs_count != labels_count:
        print(f"❌ Record count mismatch")
        return False
    
    print(f"✅ All files present with {inputs_count} records each")
    return True


def validate_label_format():
    """Validate the format of eval_outputs_labels.jsonl."""
    print("\n" + "=" * 60)
    print("3. Validating label format...")
    print("=" * 60)
    
    labels_path = Path("tests/fixtures/mlflow_eval/eval_outputs_labels.jsonl")
    
    if not labels_path.exists():
        print(f"❌ File not found: {labels_path}")
        return False
    
    required_fields = ["Overall_Verdict", "M1", "M2", "M3", "M4", "M5"]
    valid_verdicts = {"Yes", "No", "N-A"}
    
    lines = labels_path.read_text().splitlines()
    sample_size = min(5, len(lines))
    
    print(f"Checking first {sample_size} records...")
    
    for i, line in enumerate(lines[:sample_size]):
        if not line.strip():
            continue
        
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"❌ Line {i+1}: Invalid JSON: {e}")
            return False
        
        # Check required fields
        missing = [f for f in required_fields if f not in record]
        if missing:
            print(f"❌ Line {i+1}: Missing fields: {missing}")
            return False
        
        # Check verdict values
        for field in required_fields:
            value = record[field]
            if value not in valid_verdicts:
                print(f"❌ Line {i+1}: Invalid value for {field}: {value}")
                print(f"   Expected one of: {valid_verdicts}")
                return False
        
        print(f"  Record {i+1}: {json.dumps(record)}")
    
    print(f"✅ Label format valid")
    return True


def check_dependencies():
    """Check if required Python packages are installed."""
    print("\n" + "=" * 60)
    print("4. Checking dependencies...")
    print("=" * 60)
    
    required = ["mlflow", "sklearn", "matplotlib", "numpy"]
    missing = []
    
    for package in required:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} not installed")
            missing.append(package)
    
    if missing:
        print(f"\nInstall missing packages:")
        print(f"  uv add --dev mlflow scikit-learn matplotlib")
        return False
    
    return True


def check_environment():
    """Check required environment variables."""
    print("\n" + "=" * 60)
    print("5. Checking environment variables...")
    print("=" * 60)
    
    import os
    
    required_vars = ["BEDROCK_API_KEY", "BEDROCK_API_BASE_URL", "BEDROCK_MODEL"]
    missing = []
    
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Mask API key
            if "KEY" in var:
                display_value = value[:8] + "..." if len(value) > 8 else "***"
            else:
                display_value = value
            print(f"✅ {var}={display_value}")
        else:
            print(f"❌ {var} not set")
            missing.append(var)
    
    if missing:
        print(f"\nSet missing variables in .env file or export them:")
        for var in missing:
            print(f"  export {var}=<value>")
        return False
    
    return True


def main():
    print("\n" + "=" * 60)
    print("MLflow Evaluation Setup Verification")
    print("=" * 60)
    
    checks = [
        check_batch_files,
        check_consolidated_files,
        validate_label_format,
        check_dependencies,
        check_environment,
    ]
    
    results = []
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"\n❌ Check failed with error: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Passed: {passed}/{total}")
    
    if all(results):
        print("\n✅ All checks passed! Ready to run evaluation.")
        print("\nNext step:")
        print("  uv run python scripts/evaluate_test_suite_reviewer.py \\")
        print("      --fixture tests/fixtures/mlflow_eval/eval_inputs.jsonl \\")
        print("      --labels tests/fixtures/mlflow_eval/eval_outputs_labels.jsonl \\")
        print("      --run-name \"baseline-$(git rev-parse --short HEAD)\"")
        return 0
    else:
        print("\n❌ Some checks failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
