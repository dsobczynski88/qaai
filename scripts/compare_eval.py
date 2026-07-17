"""Render an actual-vs-predicted diff (compare.html) for one evaluation run.

Thin wrapper around ``qaai.eval.compare`` — given a ``predictions/<ts>/`` folder from a
``--mode run`` study, writes a self-contained side-by-side viewer that highlights every
record where the reviewer's predicted verdict/rubric deviates from the answer key. Spec
and parent dataset are auto-resolved from the folder's run_metadata.json.

Examples:
    uv run python scripts/compare_eval.py eval/datasets/test_suite/predictions/<ts>/
    uv run python scripts/compare_eval.py eval/datasets/test_suite/predictions/<ts>/ --open

Equivalent to: python -m qaai.eval.compare <predictions_dir>
"""
from qaai.eval.compare import main

if __name__ == "__main__":
    raise SystemExit(main())
