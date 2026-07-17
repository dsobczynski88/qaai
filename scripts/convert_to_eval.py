"""Produce the three-file answer-key dataset from existing QAAI data.

Subcommands:
    gold     gold_dataset_labeled.jsonl -> actual_inputs.jsonl + actual_labels.jsonl
             (optionally --synthesize-outputs to also emit an oracle actual_outputs.jsonl
              from the labels, so score-only mode runs offline).
    outputs  a live run's outputs.jsonl (full graph state) -> actual_outputs.jsonl

Examples:
    uv run python scripts/convert_to_eval.py gold \
        --input tests/fixtures/gold/gold_dataset_labeled.jsonl \
        --out eval/datasets/test_suite \
        --spec eval/specs/test_suite_reviewer.yaml --synthesize-outputs

    uv run python scripts/convert_to_eval.py outputs \
        --input logs/run-2026.../outputs.jsonl --out eval/datasets/test_suite
"""
import argparse
from pathlib import Path

from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    gold_to_eval,
    passthrough_outputs,
    synthesize_outputs,
    write_jsonl,
)


def _cmd_gold(args: argparse.Namespace) -> None:
    inputs, labels = gold_to_eval(args.input)
    out = Path(args.out)
    write_jsonl(out / ACTUAL_INPUTS_NAME, inputs)
    write_jsonl(out / ACTUAL_LABELS_NAME, labels)
    msg = f"wrote {len(inputs)} inputs + {len(labels)} labels to {out}"
    if args.synthesize_outputs:
        if not args.spec:
            raise SystemExit("--synthesize-outputs requires --spec (to know the output shape)")
        from qaai.eval.spec import load_spec

        spec = load_spec(args.spec)
        outputs = synthesize_outputs(spec, labels)
        write_jsonl(out / ACTUAL_OUTPUTS_NAME, outputs)
        msg += f" + {len(outputs)} oracle outputs"
    print(msg)


def _cmd_outputs(args: argparse.Namespace) -> None:
    rows = passthrough_outputs(args.input)
    out = Path(args.out)
    write_jsonl(out / ACTUAL_OUTPUTS_NAME, rows)
    print(f"wrote {len(rows)} outputs to {out / ACTUAL_OUTPUTS_NAME}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gold", help="convert gold_dataset_labeled.jsonl")
    g.add_argument("--input", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--spec", type=Path, help="spec (required with --synthesize-outputs)")
    g.add_argument("--synthesize-outputs", action="store_true",
                   help="also write an oracle actual_outputs.jsonl from labels (offline demo)")
    g.set_defaults(func=_cmd_gold)

    o = sub.add_parser("outputs", help="convert a run's outputs.jsonl")
    o.add_argument("--input", type=Path, required=True)
    o.add_argument("--out", type=Path, required=True)
    o.set_defaults(func=_cmd_outputs)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
