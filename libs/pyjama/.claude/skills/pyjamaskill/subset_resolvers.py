"""
Subset resolvers for Jama trace matrices.

Every trace matrix needs two lists of item IDs: one for the row axis, one for
the column axis. These helpers turn user-friendly inputs (baseline name, item
type key) into the integer item IDs that Jama's relationship endpoint
understands.

All functions take a `JamaClient` as their first argument so callers can
inject a shared, lifespan-managed client.
"""

from __future__ import annotations

from typing import Iterable


def resolve_baseline_by_name(client, project_id: int, baseline_name: str) -> int:
    """Return the baseline ID matching ``baseline_name`` in the given project.

    Jama permits duplicate baseline names within a project. If multiple
    baselines match, the most recently created one wins — mirroring what a
    human would probably expect when selecting from a list — but the ambiguity
    is worth surfacing to the user in the calling route.

    Raises:
        ValueError: if no baseline matches. The error message includes the
            available names to make the user's next action obvious.
    """
    baselines = client.get_baselines(project_id)
    matches = [b for b in baselines if b.get("name") == baseline_name]
    if not matches:
        available = sorted(b.get("name", "<unnamed>") for b in baselines)
        raise ValueError(
            f"No baseline named {baseline_name!r} in project {project_id}. "
            f"Available baselines: {available}"
        )
    # Sort newest-first; Jama's createdDate is an ISO-8601 string so
    # lexicographic sort is chronological.
    matches.sort(key=lambda b: b.get("createdDate", ""), reverse=True)
    return matches[0]["id"]


def item_ids_in_baseline(client, baseline_id: int) -> list[int]:
    """Return the list of item IDs captured in the given baseline.

    Note: ``get_baselines_versioneditems`` returns *versioned item* records,
    each of which wraps the underlying item ID in the ``item`` field. The
    outer ``id`` field is the versioned-item record's own ID and is *not*
    what the relationships endpoint expects.
    """
    versioned = client.get_baselines_versioneditems(baseline_id)
    return [v["item"] for v in versioned if "item" in v]


def resolve_item_type(client, key_or_name: str) -> int:
    """Return the item type ID matching ``key_or_name``.

    Matches on ``typeKey`` first (the short code, e.g. ``"REQ"``) and falls
    back to ``display`` (the human-readable name, e.g. ``"Requirement"``).
    This ordering lets scripts use the stable short code while still
    accepting a display name from a UI dropdown.

    Raises:
        ValueError: if no type matches.
    """
    types = client.get_item_types()
    for t in types:
        if t.get("typeKey") == key_or_name:
            return t["id"]
    for t in types:
        if t.get("display") == key_or_name:
            return t["id"]
    sample = sorted({t.get("typeKey") for t in types if t.get("typeKey")})[:20]
    raise ValueError(
        f"No item type matches {key_or_name!r}. "
        f"Sample of available typeKeys: {sample}"
    )


def item_ids_of_type_in_project(
    client,
    project_id: int,
    type_id: int,
) -> list[int]:
    """Return all item IDs of the given type in the given project.

    Uses ``get_abstract_items`` because it accepts array params for both
    project and item_type, which is what Jama's API actually wants.
    """
    items = client.get_abstract_items(
        project=[project_id],
        item_type=[type_id],
    )
    return [i["id"] for i in items]


def resolve_subset(
    client,
    project_id: int,
    *,
    baseline_name: str | None = None,
    item_type: str | None = None,
) -> list[int]:
    """High-level resolver: returns item IDs matching the given filters.

    Supplying both ``baseline_name`` and ``item_type`` intersects the two
    sets — e.g. "Requirements that appear in the 'Release 2.4' baseline".
    Supplying neither is an error; callers should never build a matrix over
    every item in a project.

    Example:
        >>> rows = resolve_subset(client, 42, baseline_name="Release 2.4", item_type="REQ")
        >>> cols = resolve_subset(client, 42, baseline_name="Release 2.4", item_type="TC")
    """
    if baseline_name is None and item_type is None:
        raise ValueError(
            "resolve_subset requires at least one of baseline_name or item_type; "
            "refusing to build a matrix over an entire project."
        )

    candidate_sets: list[set[int]] = []

    if baseline_name is not None:
        baseline_id = resolve_baseline_by_name(client, project_id, baseline_name)
        candidate_sets.append(set(item_ids_in_baseline(client, baseline_id)))

    if item_type is not None:
        type_id = resolve_item_type(client, item_type)
        candidate_sets.append(
            set(item_ids_of_type_in_project(client, project_id, type_id))
        )

    # Intersection preserves no particular order; sort for deterministic output
    # which makes matrix row/column order stable across requests.
    result = set.intersection(*candidate_sets) if candidate_sets else set()
    return sorted(result)


def fetch_item_metadata(
    client,
    item_ids: Iterable[int],
) -> dict[int, dict]:
    """Fetch a minimal metadata record for each item ID.

    Returns a dict keyed by item ID, with values containing ``documentKey``
    and ``name`` — the two fields the matrix output needs for row/column
    headers. Uses ``get_abstract_item`` per ID; this is N HTTP calls but the
    alternative (``get_abstract_items`` for the whole project and filtering)
    is usually heavier unless the subset is very large.

    For subsets larger than ~500 items, prefer fetching all items in the
    project once and filtering in Python.
    """
    metadata: dict[int, dict] = {}
    for item_id in item_ids:
        item = client.get_abstract_item(item_id)
        metadata[item_id] = {
            "id": item_id,
            "documentKey": item.get("documentKey"),
            "name": (item.get("fields") or {}).get("name") or item.get("name"),
            "itemType": item.get("itemType"),
        }
    return metadata


def get_baseline_review_link(client, baseline_id: int) -> dict | None:
    """Return Review Center metadata linked to a baseline, or None if absent.

    This is the only public REST API surface that exposes anything about
    Review Center: ``GET /baselines/{id}/reviewlink``. It returns a small
    wrapper with ``reviewKey``, ``reviewName``, ``url``, ``revisionId``,
    ``readAccess``, and ``deleted`` — a pointer to the review, not its
    contents. There is no public API for review items, comments, decisions,
    or participants.

    py-jama-rest-client does not wrap this endpoint, so this helper drops
    into the client's private ``Core`` object via name-mangled access. That
    attribute is not part of the library's public API; if
    py-jama-rest-client refactors it, this function will break. Wrap calls
    in try/except accordingly.

    Returns:
        The review-link dict if a non-deleted review is linked to the
        baseline, otherwise None. ``deleted: True`` baselines return None.

    Raises:
        AttributeError: if the client's private Core object has been
            renamed in a future version of py-jama-rest-client.
        py_jama_rest_client.client.APIException: subclasses on HTTP errors.
    """
    # Reach past the public surface. The Core object exposes .get/.post/etc.
    # against arbitrary resource paths and handles auth + retries.
    core = getattr(client, "_JamaClient__core", None)
    if core is None:
        raise AttributeError(
            "JamaClient's private Core attribute is not accessible. This "
            "helper depends on py-jama-rest-client's internal layout; "
            "upgrade or patch may be needed."
        )

    response = core.get(f"baselines/{baseline_id}/reviewlink")
    payload = response.json()
    data = payload.get("data")
    if not data:
        return None
    # A baseline without an attached review returns data with deleted=True
    # (or similar). Treat that as "no review" to give callers a clean None.
    if data.get("deleted"):
        return None
    return data
