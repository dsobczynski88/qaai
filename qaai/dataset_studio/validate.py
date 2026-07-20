"""Validate a three-file eval dataset against the live models and its eval spec.

The checks exist because a generated dataset can be structurally perfect and still
worthless. The committed pilot's ``description.md`` records an 800-row set that
was discarded after scoring **kappa 0.000** — its labels were not grounded in its
content. No validator can judge grounding; a human does that in the editor. What
this module guarantees is everything mechanical *around* that judgement: the rows
line up, the shapes match the models the pipeline will produce, the rubric is
complete, and the answer key agrees with itself.

``V050`` is the check that makes a dataset scorable by construction: it round-trips
each output row through :func:`qaai.eval.datasets.outputs_to_labels` — the exact
function the scorer uses — and compares it to the labels row. A dataset that passes
cannot disagree with itself at scoring time.

Severity policy: an **error** means the dataset is wrong or unscorable. A **warning**
means it is scorable but something a reviewer should look at (thin citations, class
imbalance, a missing ``description.md``). Only errors fail the run unless
``--strict``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Union

from pydantic import BaseModel, ValidationError

from qaai.dataset_studio.registry import (
    DatasetTypeInfo,
    assessment_key,
    dataset_type_for,
    infer_dataset_type,
    input_row_model,
    load_type_spec,
    output_row_model,
    output_row_shape,
)
from qaai.dataset_studio.rules import derive_overall_verdict, na_allowed_for
from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    outputs_to_labels,
)
from qaai.eval.spec import EvalSpec

__all__ = [
    "Finding",
    "SpecError",
    "ValidationReport",
    "CHECK_CODES",
    "LABEL_METADATA_KEYS",
    "validate_dataset",
    "validate_rows",
]


class SpecError(Exception):
    """The eval spec is missing or invalid (distinct from a missing dataset).

    Exists so the CLI can map it to its own exit code without sniffing exception
    text — a bad spec is an authoring mistake in ``eval/specs/``, not a problem with
    the dataset the user pointed at.
    """

# Non-rubric keys an actual_labels row may legitimately carry. The first three are
# authoring metadata from the generator skills; the last three are written by the
# editor. All are invisible to EvalSpec.extract_label, which reads only the verdict
# key and the rubric keys — so they ride along with the answer key without ever
# reaching the scorer.
LABEL_METADATA_KEYS = frozenset({
    "id", "class", "primary_failure", "notes",
    "reviewer_note", "reviewed_by", "reviewed_at",
})

CHECK_CODES: Dict[str, str] = {
    "V001": "files present and every line parses as JSON",
    "V002": "row counts aligned across the three files",
    "V010": "input row matches the reviewer's graph-state types",
    "V020": "output row matches the assessment model (full-shape rows only)",
    "V021": "output row carries an assessment",
    "V030": "label row keys and verdict vocabulary",
    "V031": "N-A used only on codes that permit it",
    "V040": "overall verdict agrees with its own rubric cells",
    "V041": "rubric codes complete and in spec order",
    "V050": "output row and label row agree (via the scorer's own flattener)",
    "V060": "partial=true only alongside a Yes verdict",
    "V061": "findings cite the evidence their field contracts require",
    "V070": "primary ids present and unique across rows",
    "V071": "cited ids exist in the input row",
    "V080": "companion files (description.md) present and non-empty",
    "V090": "class balance and per-cell negative coverage",
}

_ERROR_CODES = frozenset({
    "V001", "V002", "V010", "V020", "V021", "V030", "V031", "V040", "V041", "V050", "V070",
})


class Finding(BaseModel):
    """One validation result. ``path`` uses the same dotted+``[i]`` rendering as
    :mod:`qaai.dataset_studio.editlog`, so a finding greps straight out of edits.log."""

    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    row: Optional[int] = None
    file: Optional[str] = None
    path: Optional[str] = None
    before: Any = None
    after: Any = None


class ValidationReport(BaseModel):
    dataset_dir: str
    dataset_type: str
    spec_name: str
    row_counts: Dict[str, int]
    findings: List[Finding]

    @property
    def n_errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def n_warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def ok(self) -> bool:
        return self.n_errors == 0

    def to_text(self, max_findings: int = 50) -> str:
        head = [
            f"dataset : {self.dataset_dir}",
            f"type    : {self.dataset_type}  (spec: {self.spec_name})",
            "rows    : " + ", ".join(f"{k}={v}" for k, v in self.row_counts.items()),
        ]
        if not self.findings:
            head.append("")
            head.append("OK - no findings.")
            return "\n".join(head)

        ordered = sorted(
            self.findings,
            key=lambda f: (f.severity != "error", f.code, f.row if f.row is not None else -1),
        )
        shown = ordered if max_findings <= 0 else ordered[:max_findings]
        lines = head + [""]
        for f in shown:
            row = f"row {f.row:04d}" if f.row is not None else "row -----"
            where = f"  {f.file}" if f.file else ""
            path = f"  {f.path}" if f.path else ""
            lines.append(f"{f.code}  {f.severity:7s}  {row}{where}{path}: {f.message}")
        hidden = len(ordered) - len(shown)
        if hidden > 0:
            lines.append(f"... and {hidden} more (use --max-findings 0 to show all)")
        lines.append("")
        lines.append(f"{self.n_errors} error(s), {self.n_warnings} warning(s)")
        return "\n".join(lines)


def _severity(code: str) -> str:
    return "error" if code in _ERROR_CODES else "warning"


class _Collector:
    def __init__(self, checks: Optional[Set[str]], skip: Optional[Set[str]]):
        self.findings: List[Finding] = []
        self._checks = checks
        self._skip = skip or set()

    def enabled(self, code: str) -> bool:
        if code in self._skip:
            return False
        return self._checks is None or code in self._checks

    def add(self, code: str, message: str, **kw: Any) -> None:
        if not self.enabled(code):
            return
        self.findings.append(
            Finding(code=code, severity=_severity(code), message=message, **kw)
        )


def _fmt_loc(loc: Sequence[Any]) -> str:
    """Render a Pydantic error location as ``a.b[0].c``."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _validate_model(
    col: _Collector, code: str, model: type[BaseModel], row: Any, index: int, filename: str
) -> None:
    try:
        model.model_validate(row)
    except ValidationError as exc:
        for err in exc.errors():
            col.add(
                code,
                f"{err['msg']} (got {err.get('input')!r})" if err["type"] != "missing" else err["msg"],
                row=index,
                file=filename,
                path=_fmt_loc(err["loc"]),
            )


def _cells(spec: EvalSpec, out_row: Any) -> List[Dict[str, Any]]:
    """Raw finding dicts from an output row, or [] when absent."""
    rub = spec.output.rubric
    if not rub or not isinstance(out_row, dict):
        return []
    node: Any = out_row
    for part in rub.list_path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return []
    return [c for c in node if isinstance(c, dict)] if isinstance(node, list) else []


def validate_rows(
    dataset_type: str,
    spec: EvalSpec,
    inputs: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    labels: List[Dict[str, Any]],
    *,
    info: Optional[DatasetTypeInfo] = None,
    dataset_dir: str = "<memory>",
    checks: Optional[Set[str]] = None,
    skip: Optional[Set[str]] = None,
    row_range: Optional[range] = None,
    extras_present: Optional[Mapping[str, bool]] = None,
) -> ValidationReport:
    """Validate in-memory rows. Used by the CLI and by the save server pre-write."""
    info = info or dataset_type_for(dataset_type)
    col = _Collector(checks, skip)

    in_model = input_row_model(info, spec)
    out_model = output_row_model(info, spec)
    akey = assessment_key(spec)
    rub = spec.output.rubric
    codes = list(rub.codes) if rub else []
    na_ok = na_allowed_for(dataset_type)
    verdict_vocab = {spec.scoring.positive_label, spec.scoring.negative_label}
    cell_vocab = verdict_vocab | {spec.scoring.na_label}

    # ── V002 alignment ──────────────────────────────────────────────────────
    counts = {
        ACTUAL_INPUTS_NAME: len(inputs),
        ACTUAL_OUTPUTS_NAME: len(outputs),
        ACTUAL_LABELS_NAME: len(labels),
    }
    non_empty = {k: v for k, v in counts.items() if v}
    if len(set(non_empty.values())) > 1:
        col.add(
            "V002",
            "row counts differ across the three files; positional alignment is the "
            "dataset's core invariant — "
            + ", ".join(f"{k}={v}" for k, v in counts.items()),
        )

    n = max(counts.values()) if counts.values() else 0
    indices = list(row_range) if row_range is not None else range(n)

    seen_ids: Dict[str, int] = {}
    label_verdicts: List[Optional[str]] = []
    cell_negatives: Dict[str, int] = {c: 0 for c in codes}

    for i in indices:
        in_row = inputs[i] if i < len(inputs) else None
        out_row = outputs[i] if i < len(outputs) else None
        lab_row = labels[i] if i < len(labels) else None

        # ── V010 input ──────────────────────────────────────────────────────
        if in_row is not None:
            _validate_model(col, "V010", in_model, in_row, i, ACTUAL_INPUTS_NAME)

            # V070 primary id uniqueness
            pid = _primary_id(dataset_type, in_row)
            if pid is None:
                col.add("V070", "row has no primary id", row=i, file=ACTUAL_INPUTS_NAME)
            elif pid in seen_ids:
                col.add(
                    "V070",
                    f"duplicate id {pid!r} (first seen at row {seen_ids[pid]})",
                    row=i, file=ACTUAL_INPUTS_NAME, before=pid,
                )
            else:
                seen_ids[pid] = i

        # ── V021 / V020 output ──────────────────────────────────────────────
        shape = output_row_shape(spec, out_row, info) if out_row is not None else "empty"
        if out_row is not None and shape == "empty":
            col.add(
                "V021",
                f"no {akey!r} in the output row; an answer key must carry an assessment",
                row=i, file=ACTUAL_OUTPUTS_NAME, path=akey,
            )
        elif shape == "full":
            # Only full-shape rows can face the live model. The minimal oracle shape
            # produced by synthesize_outputs deliberately omits required fields.
            _validate_model(col, "V020", out_model, out_row, i, ACTUAL_OUTPUTS_NAME)

        # ── rubric cells ────────────────────────────────────────────────────
        cells = _cells(spec, out_row) if out_row is not None else []
        if cells and rub:
            got = [str(c.get(rub.code_field)) for c in cells]
            # Every MANDATORY code must be present; advisory codes are optional. The
            # committed RTM answer key carries M1-M5 and no R6 — legitimate, and the
            # spec says as much ("R6 optional; missing keys are skipped"). Order must
            # still follow the spec, so a reader can scan cells positionally.
            missing = [c for c in spec.mandatory_codes if c not in got]
            unknown = [c for c in got if c not in codes]
            order = [c for c in codes if c in got]
            if missing:
                col.add(
                    "V041",
                    f"missing mandatory rubric cell(s): {', '.join(missing)}",
                    row=i, file=ACTUAL_OUTPUTS_NAME, path=rub.list_path,
                    before=got, after=order,
                )
            if unknown:
                col.add(
                    "V041",
                    f"rubric cell(s) not declared by the spec: {', '.join(unknown)}",
                    row=i, file=ACTUAL_OUTPUTS_NAME, path=rub.list_path, before=got,
                )
            if not missing and not unknown and got != order:
                col.add(
                    "V041",
                    f"rubric cells are out of spec order: {got} (expected {order})",
                    row=i, file=ACTUAL_OUTPUTS_NAME, path=rub.list_path,
                    before=got, after=order,
                )
            for pos, cell in enumerate(cells):
                code = str(cell.get(rub.code_field))
                verdict = cell.get(rub.verdict_field)
                cpath = f"{rub.list_path}[{pos}]"
                if verdict not in cell_vocab:
                    col.add(
                        "V031",
                        f"{code}: verdict {verdict!r} is not one of {sorted(cell_vocab)}",
                        row=i, file=ACTUAL_OUTPUTS_NAME,
                        path=f"{cpath}.{rub.verdict_field}", before=verdict,
                    )
                elif verdict == spec.scoring.na_label and code not in na_ok:
                    col.add(
                        "V031",
                        f"{code} may not be N-A "
                        + (f"(only {', '.join(sorted(na_ok))} may)" if na_ok else "(this rubric has no N-A)"),
                        row=i, file=ACTUAL_OUTPUTS_NAME,
                        path=f"{cpath}.{rub.verdict_field}", before=verdict,
                    )
                if verdict == spec.scoring.negative_label:
                    cell_negatives[code] = cell_negatives.get(code, 0) + 1
                if cell.get("partial") and verdict != spec.scoring.positive_label:
                    col.add(
                        "V060",
                        f"{code}: partial=true requires verdict={spec.scoring.positive_label!r}, got {verdict!r}",
                        row=i, file=ACTUAL_OUTPUTS_NAME, path=f"{cpath}.partial",
                    )
                if shape == "full":
                    # A minimal/oracle row carries no citation fields at all, by
                    # construction — reporting their absence would be pure noise.
                    _check_citations(col, dataset_type, spec, cell, code, i, cpath, in_row)

        # ── V040 derivation ─────────────────────────────────────────────────
        if out_row is not None and cells and rub:
            rubric_map = {
                str(c.get(rub.code_field)): c.get(rub.verdict_field) for c in cells
            }
            flags = None
            if dataset_type == "test_case" and any("mandatory" in c for c in cells):
                flags = {
                    str(c.get(rub.code_field)): bool(c.get("mandatory", True)) for c in cells
                }
            derived = derive_overall_verdict(dataset_type, spec, rubric_map, mandatory_flags=flags)
            if derived is not None:
                stated = _stated_verdict(spec, out_row)
                cited = ", ".join(
                    f"{c}={rubric_map[c]}" for c in spec.mandatory_codes if c in rubric_map
                )
                if stated != derived:
                    col.add(
                        "V040",
                        f"stated {stated!r} but its own cells derive {derived!r} ({cited})",
                        row=i, file=ACTUAL_OUTPUTS_NAME, path=spec.output.verdict_path,
                        before=stated, after=derived,
                    )
                if lab_row is not None:
                    lv = lab_row.get(spec.labels.verdict_key)
                    if lv != derived:
                        col.add(
                            "V040",
                            f"label {spec.labels.verdict_key}={lv!r} but the output cells "
                            f"derive {derived!r} ({cited})",
                            row=i, file=ACTUAL_LABELS_NAME, path=spec.labels.verdict_key,
                            before=lv, after=derived,
                        )

        # ── V030 labels ─────────────────────────────────────────────────────
        if lab_row is not None:
            label_verdicts.append(lab_row.get(spec.labels.verdict_key))
            _check_label_row(col, spec, lab_row, i, codes, na_ok, verdict_vocab, cell_vocab)

        # ── V050 answer key self-agreement ──────────────────────────────────
        if out_row is not None and lab_row is not None and shape != "empty":
            flat = outputs_to_labels(spec, [out_row])[0]
            for key, want in flat.items():
                if key not in lab_row:
                    col.add(
                        "V050",
                        f"output declares {key}={want!r} but the label row has no {key!r} key",
                        row=i, file=ACTUAL_LABELS_NAME, path=key, after=want,
                    )
                elif lab_row[key] != want:
                    col.add(
                        "V050",
                        f"{key}: label {lab_row[key]!r} != output {want!r}",
                        row=i, file=ACTUAL_LABELS_NAME, path=key,
                        before=lab_row[key], after=want,
                    )

    # ── V080 companion files ────────────────────────────────────────────────
    if extras_present is not None:
        for name, present in extras_present.items():
            if not present:
                col.add("V080", f"{name} is missing or empty")

    # ── V090 balance ────────────────────────────────────────────────────────
    if label_verdicts:
        pos = sum(1 for v in label_verdicts if v == spec.scoring.positive_label)
        neg = sum(1 for v in label_verdicts if v == spec.scoring.negative_label)
        total = len(label_verdicts)
        if total >= 4 and (pos == 0 or neg == 0):
            col.add(
                "V090",
                f"single-class dataset ({pos} {spec.scoring.positive_label} / "
                f"{neg} {spec.scoring.negative_label}); a classifier study needs both",
            )
        starved = [c for c in spec.mandatory_codes if cell_negatives.get(c, 0) == 0]
        if starved and total >= 4:
            col.add(
                "V090",
                f"no {spec.scoring.negative_label} example for {', '.join(starved)}; "
                "those rubric cells cannot be scored as a classifier",
            )

    return ValidationReport(
        dataset_dir=dataset_dir,
        dataset_type=dataset_type,
        spec_name=spec.name,
        row_counts=counts,
        findings=col.findings,
    )


def _stated_verdict(spec: EvalSpec, out_row: Any) -> Any:
    node: Any = out_row
    for part in spec.output.verdict_path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def _primary_id(dataset_type: str, in_row: Mapping[str, Any]) -> Optional[str]:
    """The id that makes a row unique, per type."""
    if dataset_type == "test_suite":
        req = in_row.get("requirement")
        return (req or {}).get("req_id") if isinstance(req, dict) else None
    if dataset_type == "test_case":
        tc = in_row.get("test_case")
        return (tc or {}).get("test_id") if isinstance(tc, dict) else None
    if dataset_type == "hazard":
        hz = in_row.get("hazard")
        if isinstance(hz, dict):
            return hz.get("hazard_id") or hz.get("SHA ID Number")
    return None


def _check_label_row(
    col: _Collector,
    spec: EvalSpec,
    lab_row: Mapping[str, Any],
    index: int,
    codes: Sequence[str],
    na_ok: Iterable[str],
    verdict_vocab: Set[str],
    cell_vocab: Set[str],
) -> None:
    vkey = spec.labels.verdict_key
    if vkey not in lab_row:
        col.add("V030", f"missing {vkey!r}", row=index, file=ACTUAL_LABELS_NAME, path=vkey)
    elif lab_row[vkey] not in verdict_vocab:
        col.add(
            "V030",
            f"{vkey}={lab_row[vkey]!r} is not one of {sorted(verdict_vocab)} "
            "(the overall verdict is binary; N-A applies to cells only)",
            row=index, file=ACTUAL_LABELS_NAME, path=vkey, before=lab_row[vkey],
        )

    known = set(codes) | {vkey} | LABEL_METADATA_KEYS
    na_ok = set(na_ok)
    for key, value in lab_row.items():
        if key not in known:
            col.add(
                "V030",
                f"unknown key {key!r}; expected a rubric code, {vkey!r}, or metadata "
                f"({', '.join(sorted(LABEL_METADATA_KEYS))})",
                row=index, file=ACTUAL_LABELS_NAME, path=key,
            )
        elif key in codes:
            if value not in cell_vocab:
                col.add(
                    "V030",
                    f"{key}={value!r} is not one of {sorted(cell_vocab)}",
                    row=index, file=ACTUAL_LABELS_NAME, path=key, before=value,
                )
            elif value == spec.scoring.na_label and key not in na_ok:
                col.add(
                    "V031",
                    f"{key} may not be N-A "
                    + (f"(only {', '.join(sorted(na_ok))} may)" if na_ok else "(this rubric has no N-A)"),
                    row=index, file=ACTUAL_LABELS_NAME, path=key, before=value,
                )


def _check_citations(
    col: _Collector,
    dataset_type: str,
    spec: EvalSpec,
    cell: Mapping[str, Any],
    code: str,
    index: int,
    cpath: str,
    in_row: Optional[Mapping[str, Any]],
) -> None:
    """V061/V071 — the evidence contracts stated in the finding models' field docs.

    These are warnings: the models declare ``default_factory=list`` and do not enforce
    them, so a row that omits citations is loadable, just thin.
    """
    verdict = cell.get(spec.output.rubric.verdict_field) if spec.output.rubric else None
    yes, no = spec.scoring.positive_label, spec.scoring.negative_label

    if dataset_type == "test_suite":
        if code in {"M1", "M2", "M3"} and verdict == yes and not cell.get("cited_test_case_ids"):
            col.add(
                "V061",
                f"{code}={yes} with no cited_test_case_ids "
                "(MandatoryFinding: 'Required for M1-M3 when verdict=Yes')",
                row=index, file=ACTUAL_OUTPUTS_NAME, path=f"{cpath}.cited_test_case_ids",
            )
        if code == "M4" and verdict == no and not cell.get("uncovered_spec_ids"):
            col.add(
                "V061",
                "M4=No with no uncovered_spec_ids "
                "(MandatoryFinding: 'Populated only on M4 when verdict=No')",
                row=index, file=ACTUAL_OUTPUTS_NAME, path=f"{cpath}.uncovered_spec_ids",
            )
    elif dataset_type == "hazard":
        if verdict == no and not cell.get("unblocked_items"):
            col.add(
                "V061",
                f"{code}={no} with no unblocked_items "
                "(HazardFinding: 'Populated when verdict=No with specific missing elements')",
                row=index, file=ACTUAL_OUTPUTS_NAME, path=f"{cpath}.unblocked_items",
            )

    # V071 — cited ids must exist in the input row.
    if in_row is None:
        return
    known_tc = _known_test_case_ids(dataset_type, in_row)
    for tid in cell.get("cited_test_case_ids") or []:
        if known_tc and tid not in known_tc:
            col.add(
                "V071",
                f"{code} cites test case {tid!r}, which is not in this row's inputs",
                row=index, file=ACTUAL_OUTPUTS_NAME,
                path=f"{cpath}.cited_test_case_ids", before=tid,
            )
    known_req = _known_req_ids(dataset_type, in_row)
    for rid in cell.get("cited_req_ids") or []:
        if known_req and rid not in known_req:
            col.add(
                "V071",
                f"{code} cites requirement {rid!r}, which is not in this row's inputs",
                row=index, file=ACTUAL_OUTPUTS_NAME,
                path=f"{cpath}.cited_req_ids", before=rid,
            )


def _trace(in_row: Mapping[str, Any]) -> Mapping[str, Any]:
    hz = in_row.get("hazard")
    if isinstance(hz, dict):
        tm = hz.get("requirements_traceability")
        if isinstance(tm, dict):
            return tm
    return {}


def _known_test_case_ids(dataset_type: str, in_row: Mapping[str, Any]) -> Set[str]:
    if dataset_type == "hazard":
        items = _trace(in_row).get("test_cases") or []
    elif dataset_type == "test_case":
        tc = in_row.get("test_case")
        items = [tc] if isinstance(tc, dict) else []
    else:
        items = in_row.get("test_cases") or []
    return {t["test_id"] for t in items if isinstance(t, dict) and t.get("test_id")}


def _known_req_ids(dataset_type: str, in_row: Mapping[str, Any]) -> Set[str]:
    if dataset_type == "hazard":
        tm = _trace(in_row)
        items = list(tm.get("requirements") or []) + list(tm.get("system_requirements") or [])
    elif dataset_type == "test_case":
        items = in_row.get("requirements") or []
    else:
        req = in_row.get("requirement")
        items = [req] if isinstance(req, dict) else []
    return {r["req_id"] for r in items if isinstance(r, dict) and r.get("req_id")}


def validate_dataset(
    dataset_dir: Union[str, Path],
    *,
    dataset_type: Optional[str] = None,
    spec_path: Optional[Union[str, Path]] = None,
    checks: Optional[Set[str]] = None,
    skip: Optional[Set[str]] = None,
    row_range: Optional[range] = None,
) -> ValidationReport:
    """Load a dataset directory and validate it.

    Raises ``FileNotFoundError`` if the directory or a required JSONL is missing
    (CLI exit 2), ``KeyError`` for an unknown/uninferable type (exit 3).
    """
    d = Path(dataset_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"not a directory: {d}")

    dtype = dataset_type or infer_dataset_type(d)
    if dtype is None:
        raise KeyError(
            f"cannot infer the dataset type from {d}; pass --type explicitly"
        )
    info = dataset_type_for(dtype)
    try:
        spec = load_type_spec(info, spec_path)
    except Exception as exc:
        raise SpecError(f"cannot load the eval spec for {dtype}: {exc}") from exc

    col_findings: List[Finding] = []
    parsed: Dict[str, List[Dict[str, Any]]] = {}
    for name in (ACTUAL_INPUTS_NAME, ACTUAL_OUTPUTS_NAME, ACTUAL_LABELS_NAME):
        path = d / name
        if not path.exists():
            if name == ACTUAL_INPUTS_NAME:
                # Inputs are optional for a score-only dataset; the other two are not.
                parsed[name] = []
                col_findings.append(
                    Finding(code="V001", severity="warning", file=name,
                            message="file not found (score-only dataset?)")
                )
                continue
            raise FileNotFoundError(f"required file not found: {path}")
        parsed[name] = _read_jsonl(path, name, col_findings)

    report = validate_rows(
        dtype, spec,
        parsed[ACTUAL_INPUTS_NAME], parsed[ACTUAL_OUTPUTS_NAME], parsed[ACTUAL_LABELS_NAME],
        info=info, dataset_dir=str(d), checks=checks, skip=skip, row_range=row_range,
        extras_present={
            "description.md": (d / "description.md").exists()
            and (d / "description.md").stat().st_size > 0,
        },
    )
    report.findings = col_findings + report.findings
    return report


def _read_jsonl(path: Path, name: str, sink: List[Finding]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            sink.append(
                Finding(code="V001", severity="error", file=name, row=lineno - 1,
                        message=f"line {lineno}: invalid JSON ({exc})")
            )
    return rows
