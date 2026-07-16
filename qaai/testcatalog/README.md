# Test Catalog

A searchable, single-file HTML "book" of this repo's pytest suite. It answers, for
every collected test: **what type** it is (unit / integration / api), **what
component** it belongs to (rtm / tc / hazard / eval / shared / api), **what it
checks** (summary), **where it pulls its fixtures/inputs from**, and an **example
input/output**.

It is a pytest plugin, so the catalog is built from the tests pytest *actually
collects* — it can never drift from reality.

## Generate it

```bash
# Fast path — no tests run, no LLM calls (collection only):
uv run pytest --collect-only --test-catalog
#   -> logs/test-catalog/test_catalog.html   (open in a browser)
#   -> logs/test-catalog/test_catalog.json   (the underlying data)

# Scope it like any pytest run — the catalog tracks the selection:
uv run pytest -m unit --collect-only --test-catalog
uv run pytest tests/unit/eval --collect-only --test-catalog

# Change the output directory:
uv run pytest --collect-only --test-catalog --test-catalog-out docs/test-catalog
```

The HTML page has: a text search box (name / summary / fixtures / file), filter
chips for **type** and **component**, sortable columns, a per-row **I/O** modal
(fixtures + where each is defined + example input/output), a light/dark toggle, and
**Copy as Markdown** / **Export JSON** buttons that respect the current filter.

## Re-render without re-collecting

```bash
python -m qaai.testcatalog logs/test-catalog/test_catalog.json
```

## Curate a test's entry

Everything is auto-derived from docstrings, markers, fixtures, and parametrize
params. To hand-author a clean summary/example on any test, add the optional
marker — any field you set overrides the auto-derived value:

```python
import pytest

@pytest.mark.catalog(
    summary="Skips the RTM review when a requirement has no traced test cases",
    example_input={"requirement": {"req_id": "REQ-1", "text": "..."}, "test_cases": []},
    example_output={"review_status": "skipped", "missing_fields": ["test_cases"]},
)
def test_inputs_with_no_traced_test_cases_are_skipped(...):
    ...
```

New tests appear automatically the next time you run with `--test-catalog`; the
marker is entirely optional.

## How auto-derivation works

| Column | Source (marker wins when present) |
|--------|-----------------------------------|
| Summary | `@pytest.mark.catalog(summary=)` → function docstring → module docstring → humanized name |
| Type | `integration` / `unit` marker; `api` inferred from `tests/api/` path; else `unlabeled` |
| Component | nodeid path segment (`test_suite_reviewer`→rtm, …) |
| Fixtures / input | `item.fixturenames` resolved via the fixture manager to each fixture's defining file + docstring |
| Example input | `@pytest.mark.catalog(example_input=)` → parametrize params (`item.callspec`, e.g. the JSONL row) |
| Example output | `@pytest.mark.catalog(example_output=)` only (no literal output is captured automatically) |
