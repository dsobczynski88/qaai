"""CLI: ``python -m qaai.dataset_studio <new|ingest|sync-outputs|validate|edit>``.

Two ways in. ``new`` scaffolds an empty set for an LLM to author; ``ingest`` converts a
completed review run into one pre-filled with the model's own answers. Both land in
``eval/datasets/<type>/actual/<timestamp>/`` and are finished the same way — ``edit`` in
the browser, then ``validate``.

Exit codes are distinct so a skill or CI step can branch on them:

==  ============================================================
0   clean (warnings allowed unless ``--strict``)
1   at least one error finding
2   dataset directory or a required JSONL missing
3   usage error (unknown/uninferable type, bad ``--rows``, bad host)
4   eval spec missing or invalid
==  ============================================================
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Set

from qaai.dataset_studio.registry import DATASET_TYPES

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_MISSING = 2
EXIT_USAGE = 3
EXIT_SPEC = 4

DATASET_TYPE_CHOICES = tuple(sorted(DATASET_TYPES))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m qaai.dataset_studio",
        description="Scaffold, validate, and review QAAI eval datasets.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser(
        "new", aliases=["scaffold"],
        help="create eval/datasets/<type>/actual/<timestamp>/ with an empty three-file skeleton",
    )
    p_new.add_argument("--type", required=True, choices=DATASET_TYPE_CHOICES)
    p_new.add_argument("--base-dir", default="eval/datasets",
                       help="root under which <type>/actual/<timestamp>/ is created (default: eval/datasets)")
    p_new.add_argument("--title", default=None,
                       help="title line for description.md (default: '<type> dataset — <timestamp>')")
    p_new.add_argument("--from-dataset", default=None, metavar="DIR",
                       help="seed the new folder by copying an existing dataset's three JSONL files")
    p_new.add_argument("--quiet", action="store_true",
                       help="print only the created directory path (for scripting from a skill)")

    p_val = sub.add_parser("validate", help="validate a dataset against the live models")
    p_val.add_argument("dataset_dir")
    p_val.add_argument("--type", default=None, choices=DATASET_TYPE_CHOICES,
                       help="default: inferred from the dataset path")
    p_val.add_argument("--spec", default=None, help="override the eval spec YAML path")
    p_val.add_argument("--rows", default=None, metavar="A:B",
                       help="validate only rows [A,B) (0-indexed, python-slice semantics)")
    p_val.add_argument("--checks", default=None, metavar="CODES",
                       help="comma-separated check codes to run (default: all)")
    p_val.add_argument("--skip", default=None, metavar="CODES",
                       help="comma-separated check codes to skip")
    p_val.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p_val.add_argument("--json", dest="as_json", action="store_true",
                       help="emit machine-readable findings instead of the text report")
    p_val.add_argument("--max-findings", type=int, default=50,
                       help="truncate the text report after N findings (0 = unlimited)")
    p_val.add_argument("--list-checks", action="store_true",
                       help="print the check catalog and exit")

    p_sync = sub.add_parser(
        "sync-outputs",
        help="regenerate actual_outputs.jsonl from actual_labels.jsonl (oracle shape)",
    )
    p_sync.add_argument("dataset_dir")
    p_sync.add_argument("--type", default=None, choices=DATASET_TYPE_CHOICES)
    p_sync.add_argument("--spec", default=None)
    p_sync.add_argument("--force", action="store_true",
                        help="overwrite a non-empty actual_outputs.jsonl")

    p_ing = sub.add_parser(
        "ingest",
        help="turn a completed run (logs/run-<ts>/ or predictions/<ts>/) into a reviewable dataset",
    )
    p_ing.add_argument("run_dir", nargs="?", default=None,
                       help="run folder; omit only if --outputs is given")
    p_ing.add_argument("--type", default=None, choices=DATASET_TYPE_CHOICES,
                       help="default: detected from the assessment key in the output rows")
    p_ing.add_argument("--spec", default=None, help="override the eval spec YAML path")
    p_ing.add_argument("--inputs", default=None, metavar="PATH",
                       help="override the run's inputs JSONL")
    p_ing.add_argument("--outputs", default=None, metavar="PATH",
                       help="override the run's outputs JSONL")
    p_ing.add_argument("--base-dir", default="eval/datasets",
                       help="root under which <type>/actual/<timestamp>/ is created")
    p_ing.add_argument("--out", default=None, metavar="DIR",
                       help="write to this exact directory instead of a fresh timestamped one")
    p_ing.add_argument("--reviewer", default=None,
                       help="name recorded in source.json and edits.log (default: the OS user)")
    p_ing.add_argument("--quiet", action="store_true",
                       help="print only the created directory path")
    p_ing.add_argument("--edit", action="store_true",
                       help="open the editor on the new dataset once it is written")
    p_ing.add_argument("--port", type=int, default=0, help="with --edit: 0 = ephemeral port")
    p_ing.add_argument("--no-browser", action="store_true",
                       help="with --edit: print the URL, do not open it")
    p_ing.add_argument("--timeout", type=int, default=3600,
                       help="with --edit: shut down after N seconds idle (0 = never)")

    p_edit = sub.add_parser("edit", help="serve the sample editor on localhost")
    p_edit.add_argument("dataset_dir")
    p_edit.add_argument("--type", default=None, choices=DATASET_TYPE_CHOICES)
    p_edit.add_argument("--spec", default=None)
    p_edit.add_argument("--host", default="127.0.0.1",
                        help="bind address; loopback only")
    p_edit.add_argument("--port", type=int, default=0, help="0 = OS-assigned ephemeral port")
    p_edit.add_argument("--no-browser", action="store_true", help="print the URL, do not open it")
    p_edit.add_argument("--read-only", action="store_true", help="disable the save endpoints")
    p_edit.add_argument("--allow-invalid", action="store_true",
                        help="permit saving rows that fail validation (still logged)")
    p_edit.add_argument("--reviewer", default=None,
                        help="name recorded in edits.log (default: the OS user)")
    p_edit.add_argument("--timeout", type=int, default=3600,
                        help="shut down after N seconds idle (0 = never)")
    p_edit.add_argument("--dump-html", default=None, metavar="PATH",
                        help="write the editor HTML to PATH and exit without serving")
    return ap


def _parse_codes(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw:
        return None
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def _parse_rows(raw: Optional[str]) -> Optional[range]:
    if not raw:
        return None
    if ":" not in raw:
        raise ValueError(f"--rows must look like A:B, got {raw!r}")
    a, b = raw.split(":", 1)
    start = int(a) if a.strip() else 0
    stop = int(b) if b.strip() else 1_000_000
    if start < 0 or stop < start:
        raise ValueError(f"--rows range is empty or negative: {raw!r}")
    return range(start, stop)


def _cmd_new(args: argparse.Namespace) -> int:
    from qaai.dataset_studio.scaffold import scaffold_dataset

    try:
        out = scaffold_dataset(
            args.type, base_dir=args.base_dir, title=args.title, seed_from=args.from_dataset
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MISSING

    if args.quiet:
        print(out)
    else:
        print(f"created {out}")
        for name in sorted(p.name for p in out.iterdir()):
            print(f"  {name}")
        print()
        print("Next: write the three JSONL files, then validate:")
        print(f"  uv run python -m qaai.dataset_studio validate {out}")
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    from qaai.dataset_studio.validate import CHECK_CODES, SpecError, validate_dataset

    if args.list_checks:
        for code, desc in sorted(CHECK_CODES.items()):
            print(f"{code}  {desc}")
        return EXIT_OK

    try:
        row_range = _parse_rows(args.rows)
        checks = _parse_codes(args.checks)
        skip = _parse_codes(args.skip)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    unknown = (checks or set()) - set(CHECK_CODES)
    if unknown:
        print(f"error: unknown check code(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return EXIT_USAGE

    try:
        report = validate_dataset(
            args.dataset_dir, dataset_type=args.type, spec_path=args.spec,
            checks=checks, skip=skip, row_range=row_range,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MISSING
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SPEC
    except KeyError as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text(max_findings=args.max_findings))

    if report.n_errors:
        return EXIT_FINDINGS
    if args.strict and report.n_warnings:
        return EXIT_FINDINGS
    return EXIT_OK


def _cmd_sync_outputs(args: argparse.Namespace) -> int:
    """Derive actual_outputs.jsonl from actual_labels.jsonl.

    Hand-authoring the outputs file alongside the labels is the single most common way
    a generated dataset ends up disagreeing with itself (``V050``). Deriving it with
    the same :func:`~qaai.eval.datasets.synthesize_outputs` the committed pilot used
    makes that disagreement impossible by construction.
    """
    from qaai.dataset_studio.registry import (
        dataset_type_for,
        infer_dataset_type,
        load_type_spec,
    )
    from qaai.dataset_studio.writer import write_jsonl_atomic
    from qaai.eval.datasets import (
        ACTUAL_LABELS_NAME,
        ACTUAL_OUTPUTS_NAME,
        load_jsonl,
        synthesize_outputs,
    )

    d = Path(args.dataset_dir)
    dtype = args.type or infer_dataset_type(d)
    if dtype is None:
        print(f"error: cannot infer the dataset type from {d}; pass --type", file=sys.stderr)
        return EXIT_USAGE

    labels_path = d / ACTUAL_LABELS_NAME
    if not labels_path.exists():
        print(f"error: {labels_path} not found", file=sys.stderr)
        return EXIT_MISSING

    outputs_path = d / ACTUAL_OUTPUTS_NAME
    if outputs_path.exists() and outputs_path.stat().st_size and not args.force:
        print(
            f"error: {outputs_path} is not empty; pass --force to overwrite",
            file=sys.stderr,
        )
        return EXIT_USAGE

    spec = load_type_spec(dataset_type_for(dtype), args.spec)
    labels = load_jsonl(labels_path)
    write_jsonl_atomic(outputs_path, synthesize_outputs(spec, labels))
    print(f"wrote {len(labels)} rows to {outputs_path}")
    print(f"Next: uv run python -m qaai.dataset_studio validate {d}")
    return EXIT_OK


def _cmd_ingest(args: argparse.Namespace) -> int:
    from qaai.dataset_studio.ingest import IngestError, ingest_run, write_ingested
    from qaai.dataset_studio.validate import validate_dataset

    if args.run_dir is None and args.outputs is None:
        print("error: give a run directory, or --outputs", file=sys.stderr)
        return EXIT_USAGE

    try:
        result = ingest_run(
            args.run_dir or ".",
            dataset_type=args.type,
            spec_path=args.spec,
            inputs=args.inputs,
            outputs=args.outputs,
            reviewer=args.reviewer,
        )
        out = write_ingested(
            result, out_dir=args.out, base_dir=args.base_dir, reviewer=args.reviewer
        )
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MISSING
    except KeyError as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.quiet:
        print(out)
    else:
        print(f"ingested {result.n_records} rows -> {out}")
        print(f"  type:    {result.dataset_type}")
        print(f"  source:  {result.provenance.get('source_outputs_path')}")
        if result.skipped:
            print(f"  skipped: {len(result.skipped)} input item(s) with no output row")
        report = validate_dataset(out, dataset_type=result.dataset_type, spec_path=args.spec)
        print()
        print(report.to_text(max_findings=20))
        print()
        print("Labels are the MODEL's answers, not ground truth. Review them:")
        print(f"  uv run python -m qaai.dataset_studio edit {out}")

    if args.edit:
        from qaai.dataset_studio.server import serve_editor

        return serve_editor(
            out,
            dataset_type=result.dataset_type,
            spec_path=args.spec,
            port=args.port,
            open_browser=not args.no_browser,
            reviewer=args.reviewer,
            idle_timeout=args.timeout,
            # An ingested run carries the model's own contradictions; blocking the save
            # would trap the reviewer with no way to record the corrections that fix them.
            allow_invalid=True,
        )
    return EXIT_OK


def _cmd_edit(args: argparse.Namespace) -> int:
    from qaai.dataset_studio.server import serve_editor

    try:
        return serve_editor(
            args.dataset_dir,
            dataset_type=args.type,
            spec_path=args.spec,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            read_only=args.read_only,
            allow_invalid=args.allow_invalid,
            reviewer=args.reviewer,
            idle_timeout=args.timeout,
            dump_html=args.dump_html,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_MISSING
    except (KeyError, ValueError) as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return EXIT_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("new", "scaffold"):
        return _cmd_new(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "sync-outputs":
        return _cmd_sync_outputs(args)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "edit":
        return _cmd_edit(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
