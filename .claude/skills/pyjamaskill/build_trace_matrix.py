"""
Pure transformation: relationships -> sparse trace matrix.

Deliberately has no dependency on the JamaClient or any HTTP layer. Feed it
item ID lists, a list of relationship dicts, and lookup maps; it returns the
canonical matrix structure. Unit tests can drive it with plain dicts.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_trace_matrix(
    *,
    project_id: int,
    row_ids: Sequence[int],
    col_ids: Sequence[int],
    row_metadata: Mapping[int, dict],
    col_metadata: Mapping[int, dict],
    relationships: Sequence[Mapping[str, Any]],
    rel_type_names: Mapping[int, str] | None = None,
    source_axis: Mapping[str, Any] | None = None,
    target_axis: Mapping[str, Any] | None = None,
    directional: bool = True,
) -> dict:
    """Build a sparse trace matrix from raw Jama data.

    Args:
        project_id: Jama project ID the matrix belongs to. Included in the
            output for provenance; not used for filtering (callers should
            pass relationships already scoped to the project).
        row_ids: Item IDs along the row (source) axis.
        col_ids: Item IDs along the column (target) axis.
        row_metadata: Mapping from item ID to a dict with at least
            ``documentKey`` and ``name``. Missing IDs render with empty
            fields — non-fatal, but usually indicates upstream bug.
        col_metadata: Same shape as ``row_metadata``, for columns.
        relationships: The full list of relationship dicts from
            ``JamaClient.get_relationships``. Irrelevant relationships (not
            touching any row × column pair) are filtered out here.
        rel_type_names: Optional map from relationship type ID to name. If
            provided, each cell entry gets a ``typeName`` field. Callers
            should fetch this once per process from
            ``get_relationship_types`` and reuse.
        source_axis: Optional metadata describing how the row axis was
            selected (e.g. ``{"selector": "item_type", "value": "REQ"}``).
            Echoed back in the output for the client.
        target_axis: Same, for columns.
        directional: If True (default), cell (r, c) is populated only when a
            relationship goes ``from=r, to=c``. If False, both directions
            count. Directional matches Jama's upstream/downstream semantics;
            turn it off only for peer-level relationships.

    Returns:
        A dict with the canonical structure described in SKILL.md.
    """
    row_set = set(row_ids)
    col_set = set(col_ids)
    rel_type_names = rel_type_names or {}

    cells: dict[int, dict[int, list[dict]]] = {}

    for rel in relationships:
        from_id = rel.get("fromItem")
        to_id = rel.get("toItem")
        type_id = rel.get("relationshipType")

        # Fast path: is this relationship's endpoints relevant to our axes?
        pairs: list[tuple[int, int]] = []
        if from_id in row_set and to_id in col_set:
            pairs.append((from_id, to_id))
        if not directional and from_id in col_set and to_id in row_set:
            pairs.append((to_id, from_id))

        if not pairs:
            continue

        entry = {
            "relationshipId": rel.get("id"),
            "typeId": type_id,
            "typeName": rel_type_names.get(type_id) if type_id is not None else None,
            "suspect": rel.get("suspect", False),
        }

        for row_id, col_id in pairs:
            cells.setdefault(row_id, {}).setdefault(col_id, []).append(entry)

    # Build row/column header lists. Preserve the input order so callers can
    # control sort order upstream.
    rows = [_header(row_id, row_metadata) for row_id in row_ids]
    columns = [_header(col_id, col_metadata) for col_id in col_ids]

    populated_cells = sum(len(cols) for cols in cells.values())
    rows_with_coverage = sum(1 for r in row_ids if r in cells and cells[r])

    return {
        "project_id": project_id,
        "source_axis": dict(source_axis) if source_axis else None,
        "target_axis": dict(target_axis) if target_axis else None,
        "directional": directional,
        "rows": rows,
        "columns": columns,
        "cells": cells,
        "summary": {
            "rows": len(rows),
            "columns": len(columns),
            "populated_cells": populated_cells,
            "rows_with_coverage": rows_with_coverage,
            "rows_without_coverage": len(rows) - rows_with_coverage,
        },
    }


def _header(item_id: int, metadata: Mapping[int, dict]) -> dict:
    meta = metadata.get(item_id, {})
    return {
        "id": item_id,
        "documentKey": meta.get("documentKey"),
        "name": meta.get("name"),
    }


def matrix_to_dense_rows(matrix: dict) -> list[list[str]]:
    """Flatten the sparse matrix into rows suitable for CSV/Excel export.

    The first row is the header (empty corner + column document keys).
    Subsequent rows are [row document key, cell, cell, ...] where each cell
    is a comma-separated list of relationship type names, or empty.

    This is the form ``csv.writer`` expects and the form `openpyxl` wants
    when writing a worksheet row-by-row.
    """
    columns = matrix["columns"]
    rows = matrix["rows"]
    cells = matrix["cells"]

    header = [""] + [c.get("documentKey") or str(c["id"]) for c in columns]
    dense: list[list[str]] = [header]

    for row in rows:
        row_id = row["id"]
        line = [row.get("documentKey") or str(row_id)]
        row_cells = cells.get(row_id, {})
        for col in columns:
            col_cell_list = row_cells.get(col["id"], [])
            # Join type names; fall back to a marker when unknown, to avoid
            # printing "None" in the CSV.
            names = [c.get("typeName") or "link" for c in col_cell_list]
            line.append(", ".join(names))
        dense.append(line)

    return dense
