#!/usr/bin/env python
"""Harvest ``failed_parse_*.txt`` dumps into the malformed-payload regression corpus.

Every time an LLM response fails to parse, ``BaseLLMNode._dump_failed_parse``
(qaai/agents/shared/nodes.py) writes the untruncated raw content to
``logs/**/failed_parse_<Node>_<ts>.txt``. This script turns those real-world
malformations into permanent regression fixtures:

  1. scans the run dirs for ``failed_parse_*`` files,
  2. classifies each by node -> target Pydantic model,
  3. dedupes by content hash,
  4. writes each UNIQUE payload to
     ``tests/fixtures/malformed/<model>/<hash>.json`` with a sibling
     ``<hash>.meta.json`` recording source node, first-seen timestamp, and whether
     the current repair pipeline recovers it (``repairable``).

The parametrized replay test (tests/unit/shared/test_malformed_replay.py) then pins
each shape's behavior forever. Adding the next malformation is mechanical: run this
script, commit the new fixture, extend a repair fn if ``repairable`` is False but
should be True.

Usage:
    python scripts/harvest_failed_parses.py [--dry-run] [--logs DIR ...]

Only dumps for KNOWN models (see NODE_TO_MODEL) become fixtures; dumps from other
nodes are counted and reported but not written (they have no replay target yet).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "malformed"
DEFAULT_SCAN_DIRS = ["logs", "shared/runs"]

# Node class name -> the Pydantic model its output is validated against. Only these
# produce replayable fixtures (the replay test needs a model to validate against).
NODE_TO_MODEL = {
    "DecomposerNode": "DecomposedRequirement",
    "AggregatorNode": "TestCaseAssessment",
}

_FNAME_RE = re.compile(r"failed_parse_([A-Za-z0-9]+)_(\d{8}_\d{6})(?:_\d+)?\.txt$")


def _iter_dumps(scan_dirs: list[Path]):
    for base in scan_dirs:
        if not base.exists():
            continue
        for path in base.rglob("failed_parse_*.txt"):
            m = _FNAME_RE.search(path.name)
            if m:
                yield path, m.group(1), m.group(2)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _check_repairable(model_name: str, raw: str) -> Optional[bool]:
    """Run the raw dump through the real parse+repair pipeline; True if it recovers
    to a validated model, False if it does not, None if the model can't be resolved."""
    try:
        from qaai.agents.shared.nodes import BaseLLMNode
        from qaai.agents.shared.core import DecomposedRequirement
        from qaai.agents.test_case_reviewer.core import TestCaseAssessment

        models = {
            "DecomposedRequirement": DecomposedRequirement,
            "TestCaseAssessment": TestCaseAssessment,
        }
        model = models.get(model_name)
        if model is None:
            return None

        class _Msg:  # minimal OpenAI-response shim
            content = raw

        class _Choice:
            message = _Msg()

        class _Result:
            choices = [_Choice()]

        # Suppress the parser's own failed-parse dumping so probing repairability
        # doesn't litter logs/ with new dumps on every harvest run.
        original_dump = BaseLLMNode._dump_failed_parse  # underlying function (staticmethod unwraps on access)
        BaseLLMNode._dump_failed_parse = staticmethod(lambda *a, **k: None)
        try:
            return BaseLLMNode._parse_llm_response(_Result(), model, "harvest") is not None
        finally:
            BaseLLMNode._dump_failed_parse = staticmethod(original_dump)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would be written; write nothing")
    ap.add_argument("--logs", nargs="*", default=DEFAULT_SCAN_DIRS, help="dirs to scan (default: logs shared/runs)")
    args = ap.parse_args()

    scan_dirs = [(REPO_ROOT / d) for d in args.logs]

    seen: dict[tuple[str, str], dict] = {}  # (model, hash) -> record
    skipped_nodes: dict[str, int] = {}
    total = 0

    for path, node, ts in _iter_dumps(scan_dirs):
        total += 1
        model = NODE_TO_MODEL.get(node)
        if model is None:
            skipped_nodes[node] = skipped_nodes.get(node, 0) + 1
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        h = _content_hash(raw)
        key = (model, h)
        first_seen = ts
        if key in seen and seen[key]["first_seen"] <= first_seen:
            continue  # keep earliest occurrence
        seen[key] = {
            "raw": raw,
            "model": model,
            "hash": h,
            "source_node": node,
            "first_seen": first_seen,
            "source_file": str(path.relative_to(REPO_ROOT)),
        }

    written = 0
    for (model, h), rec in sorted(seen.items()):
        out_dir = FIXTURE_ROOT / model
        payload_path = out_dir / f"{h}.json"
        meta_path = out_dir / f"{h}.meta.json"
        if payload_path.exists():
            continue  # already in the corpus — never overwrite a curated fixture
        repairable = _check_repairable(model, rec["raw"])
        meta = {
            "model": model,
            "source_node": rec["source_node"],
            "first_seen": datetime.strptime(rec["first_seen"], "%Y%m%d_%H%M%S").isoformat(),
            "source_file": rec["source_file"],
            "repairable": repairable,
        }
        label = "REPAIRABLE" if repairable else ("UNREPAIRABLE" if repairable is False else "UNKNOWN")
        print(f"[{'DRY' if args.dry_run else 'NEW'}] {model}/{h}.json  ({label}, from {rec['source_node']} @ {rec['first_seen']})")
        if not args.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            payload_path.write_text(rec["raw"], encoding="utf-8")
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            written += 1

    print(f"\nScanned {total} dump(s); {len(seen)} unique for known models; "
          f"{'would write' if args.dry_run else 'wrote'} {written} new fixture(s).")
    if skipped_nodes:
        print("Skipped dumps from nodes without a replay target (add to NODE_TO_MODEL to include):")
        for node, n in sorted(skipped_nodes.items()):
            print(f"  {node}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
