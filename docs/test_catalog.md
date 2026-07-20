# Test Catalog

<div class="meta">QAAI (qaai) · plugins/qaai_testcatalog · pytest flag --test-catalog · generated from the codebase 2026-07-16</div>

The test catalog is a searchable, single-file HTML "book" of this repo's pytest suite. For every collected test it answers: **what type** it is (unit / integration / api), **what component** it belongs to (rtm / tc / hazard / eval / shared / api), **what it checks**, **where it pulls its fixtures from**, and an **example input/output**.

<div class="note"><strong>It cannot drift.</strong> The catalog is built from
<code>session.items</code> — the tests pytest <em>actually collected</em> — rather than from a
hand-maintained list <span class="src">plugins/qaai_testcatalog/plugin.py:262-265</span>.
If a test exists, it is in the catalog; if it was deselected, it is not.</div>

## Overview

This is a **pytest plugin**: a Python package registered through the `pytest11` entry point, so its flags are always available under `uv run pytest` with no `-p` needed <span class="src">pyproject.toml:67-70</span>. It is a **no-op unless `--test-catalog` is passed** — `pytest_collection_finish` returns immediately otherwise <span class="src">plugin.py:257-260</span>.

<div class="note warn"><strong>Not a Claude Code plugin.</strong> Despite living under
<code>plugins/</code> next to <code>qaai-mlflow-eval</code>, this is a different kind of plugin
entirely. It has no <code>.claude-plugin/plugin.json</code> and is not listed in
<code>.claude-plugin/marketplace.json</code>, so <code>/plugin install</code> does not apply to
it. It is installed as a normal Python dependency by <code>uv sync</code>. Only
<code>qaai-mlflow-eval</code> is a Claude Code plugin — see
<a href="mlflow.html#install">MLflow evaluation → Install &amp; activate</a>.</div>

Nothing in the plugin runs a test, so pairing the flag with `--collect-only` produces the catalog fast and offline — **no LLM calls, no `.env` needed** <span class="src">plugin.py:1-12</span>.

<pre class="diagram"><code>pytest --collect-only --test-catalog
   │
   ├─ pytest_collection_modifyitems ─→ _item_to_record(item)  × N     (plugin.py:220)
   │                                     docstrings, markers,
   │                                     fixtures, callspec params
   ▼
   pytest_collection_finish ─→ write_catalog(records, out_dir)        (render.py:53)
                                 ├─→ logs/test-catalog/test_catalog.json
                                 └─→ logs/test-catalog/test_catalog.html</code></pre>

<h2 id="generate">Generating the catalog</h2>

The fast path collects without running anything:

```
# No tests run, no LLM calls (collection only):
uv run pytest --collect-only --test-catalog
#   -> logs/test-catalog/test_catalog.html   (open in a browser)
#   -> logs/test-catalog/test_catalog.json   (the underlying data)
```

Scope it with any normal pytest selector — the catalog tracks the selection exactly, because it reads the post-deselection item list <span class="src">plugin.py:262-265</span>:

```
uv run pytest -m unit --collect-only --test-catalog          # only the unit suite
uv run pytest tests/unit/eval --collect-only --test-catalog  # only one directory
uv run pytest -k hazard --collect-only --test-catalog        # only matching names
```

On finish it prints one line naming both artifacts <span class="src">plugin.py:286-291</span>:

```
test catalog: 328 tests -> logs/test-catalog/test_catalog.html  (data: logs/test-catalog/test_catalog.json)
```

<div class="note"><strong>Catalog errors never break collection.</strong> If a single item fails
to convert, the plugin records a placeholder row carrying the error text rather than raising
<span class="src">plugin.py:266-278</span> — a broken catalog must never fail your test run.</div>

<h2 id="flags">CLI reference</h2>

The plugin registers exactly two options, in the `test-catalog` option group <span class="src">plugin.py:37-53</span>:

<table>
<thead><tr><th>Flag</th><th>Action</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>--test-catalog</code></td><td><code>store_true</code></td><td><code>False</code></td><td>Emit the catalog. Without it the plugin does nothing at all.</td></tr>
<tr><td><code>--test-catalog-out DIR</code></td><td><code>store</code></td><td><code>logs/test-catalog</code></td><td>Output directory for <code>test_catalog.json</code> / <code>test_catalog.html</code>. Created if missing <span class="src">render.py:64</span>.</td></tr>
</tbody></table>

```
uv run pytest --collect-only --test-catalog --test-catalog-out docs/test-catalog
```

<h2 id="page">What the page gives you</h2>

The HTML is fully self-contained — CSS and JS are inlined at import time from `assets/`, so there are no external dependencies and it opens straight from disk <span class="src">plugins/qaai_testcatalog/render.py:24</span>. It offers:

<table>
<thead><tr><th>Feature</th><th>What it does</th></tr></thead>
<tbody>
<tr><td>Search box</td><td>Free-text over name / summary / fixtures / file <span class="src">assets/layout.html:24</span></td></tr>
<tr><td>Filter chips</td><td>Narrow by <strong>type</strong> and <strong>component</strong> <span class="src">assets/catalog.js:29</span></td></tr>
<tr><td>Sortable columns</td><td>Click a header to sort the table</td></tr>
<tr><td>Per-row I/O modal</td><td>Fixtures, where each is defined, and the example input/output <span class="src">assets/catalog.js:110</span></td></tr>
<tr><td>Light/dark toggle</td><td>Matches the rest of the QAAI viewers <span class="src">assets/catalog.js:173-176</span></td></tr>
<tr><td>Copy as Markdown / Export JSON</td><td>Both respect the current filter <span class="src">assets/layout.html:18-19</span></td></tr>
</tbody></table>

<h2 id="derivation">How each row is derived</h2>

Everything is auto-derived from what pytest already knows — docstrings, markers, the fixture manager, and parametrize params <span class="src">plugin.py:220-254</span>. The `@pytest.mark.catalog` marker always wins when a field is set.

<table>
<thead><tr><th>Column</th><th>Source (marker wins when present)</th></tr></thead>
<tbody>
<tr><td>Summary</td><td><code>catalog(summary=)</code> → function docstring → module docstring → humanized test name <span class="src">plugin.py:226-231</span></td></tr>
<tr><td>Type</td><td><code>integration</code> / <code>unit</code> marker (first match wins), else <code>api</code> when the nodeid contains <code>/api/</code>, else <code>unlabeled</code> <span class="src">plugin.py:85-91</span></td></tr>
<tr><td>Component</td><td>First matching nodeid path segment <span class="src">plugin.py:27-34</span></td></tr>
<tr><td>Fixtures / input</td><td><code>item.fixturenames</code> resolved via the fixture manager to each fixture's defining file + docstring first line; pytest built-ins are filtered out <span class="src">plugin.py:122-173</span></td></tr>
<tr><td>Example input</td><td><code>catalog(example_input=)</code> → parametrize params (<code>item.callspec.params</code>, e.g. the JSONL row) <span class="src">plugin.py:205-211</span></td></tr>
<tr><td>Example output</td><td><code>catalog(example_output=)</code> <strong>only</strong> — no literal output is ever captured automatically <span class="src">plugin.py:214-217</span></td></tr>
<tr><td>Skip reason</td><td>The <code>reason=</code> of a <code>skip</code> / <code>skipif</code> marker <span class="src">plugin.py:102-113</span></td></tr>
</tbody></table>

### Component mapping

Checked in order against the nodeid; first hit wins, else `other` <span class="src">plugin.py:27-34</span>:

<table>
<thead><tr><th>nodeid segment</th><th>Component label</th></tr></thead>
<tbody>
<tr><td><code>test_suite_reviewer</code></td><td><code>rtm</code></td></tr>
<tr><td><code>test_case_reviewer</code></td><td><code>tc</code></td></tr>
<tr><td><code>hazard_risk_reviewer</code></td><td><code>hazard</code></td></tr>
<tr><td><code>eval</code></td><td><code>eval</code></td></tr>
<tr><td><code>shared</code></td><td><code>shared</code></td></tr>
<tr><td><code>/api/</code></td><td><code>api</code></td></tr>
</tbody></table>

<h2 id="curate">Curating an entry</h2>

The marker is **entirely optional** — new tests appear automatically. Use it only to hand-author a clearer summary or a realistic example. Every field is optional and each one independently overrides the auto-derived value; the marker is registered in `pytest_configure` so it never raises an unknown-marker warning <span class="src">plugin.py:56-62</span>.

```
import pytest

@pytest.mark.catalog(
    summary="Skips the RTM review when a requirement has no traced test cases",
    example_input={"requirement": {"req_id": "REQ-1", "text": "..."}, "test_cases": []},
    example_output={"review_status": "skipped", "missing_fields": ["test_cases"]},
)
def test_inputs_with_no_traced_test_cases_are_skipped(...):
    ...
```

<table>
<thead><tr><th>Field</th><th>Overrides</th></tr></thead>
<tbody>
<tr><td><code>summary=</code></td><td>The docstring chain</td></tr>
<tr><td><code>example_input=</code></td><td>The parametrize params</td></tr>
<tr><td><code>example_output=</code></td><td>Nothing — this is the <em>only</em> way to show an output</td></tr>
</tbody></table>

Rows set by the marker are flagged `curated: true` in the JSON <span class="src">plugin.py:251</span>. Marker payloads and parametrize params are coerced JSON-safe on a best-effort basis, including Pydantic models via `model_dump()` <span class="src">plugin.py:186-202</span>.

<h2 id="rerender">Re-rendering offline</h2>

To rebuild the HTML after an asset/template change without re-collecting, feed the saved JSON back through the module entry point <span class="src">plugins/qaai_testcatalog/__main__.py:19-33</span>:

```
python -m qaai_testcatalog logs/test-catalog/test_catalog.json
#   -> wrote logs/test-catalog/test_catalog.html

python -m qaai_testcatalog logs/test-catalog/test_catalog.json -o docs/catalog.html
```

<table>
<thead><tr><th>Argument</th><th>Required</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>json_path</code></td><td>yes (positional)</td><td>—</td><td>Path to <code>test_catalog.json</code></td></tr>
<tr><td><code>-o</code>, <code>--output</code></td><td>no</td><td><code>test_catalog.html</code> next to the JSON</td><td>Output HTML path</td></tr>
</tbody></table>

Exits `2` with `error: <path> does not exist` if the JSON is missing <span class="src">__main__.py:27-29</span>.

<h2 id="registration">How it is registered</h2>

The `pytest11` entry point is what makes the flags always available — pytest discovers the plugin from installed package metadata, so no `-p qaai_testcatalog` and no `conftest.py` wiring is needed <span class="src">pyproject.toml:67-70</span>:

```
# Pytest plugin that emits a searchable HTML test catalog. Registering it here makes
# the --test-catalog flag always available under `uv run pytest` (no -p needed).
[project.entry-points.pytest11]
qaai_testcatalog = "qaai_testcatalog.plugin"
```

The package lives under `plugins/` but still ships inside the qaai wheel. Listing `packages` explicitly disables hatchling's auto-detection, which is why `qaai` must be named alongside it <span class="src">pyproject.toml:78-82</span>:

```
[tool.hatch.build.targets.wheel]
packages = ["qaai", "plugins/qaai_testcatalog"]
```

<div class="note warn"><strong>The entry point only takes effect on install.</strong> It is
resolved from installed metadata, so a fresh clone needs <code>uv sync --frozen</code> before
<code>--test-catalog</code> is recognised. If pytest reports <code>unrecognized arguments:
--test-catalog</code>, re-sync.</div>

<h2 id="layout">Package layout</h2>

<table>
<thead><tr><th>Path</th><th>Role</th></tr></thead>
<tbody>
<tr><td><code>plugins/qaai_testcatalog/plugin.py</code></td><td>The pytest hooks: <code>pytest_addoption</code>, <code>pytest_configure</code>, <code>pytest_collection_finish</code>, plus every auto-derivation rule</td></tr>
<tr><td><code>plugins/qaai_testcatalog/render.py</code></td><td><code>write_catalog()</code> / <code>write_catalog_from_json()</code> — inlines the assets and writes the JSON + HTML pair</td></tr>
<tr><td><code>plugins/qaai_testcatalog/__main__.py</code></td><td>The <code>python -m qaai_testcatalog</code> re-render CLI</td></tr>
<tr><td><code>plugins/qaai_testcatalog/assets/</code></td><td><code>layout.html</code>, <code>catalog.css</code>, <code>catalog.js</code> — inlined into the output, no external deps</td></tr>
<tr><td><code>plugins/qaai_testcatalog/README.md</code></td><td>The short version of this page, next to the code</td></tr>
<tr><td><code>logs/test-catalog/</code></td><td>Default output directory (gitignored run artifacts)</td></tr>
</tbody></table>

The HTML reuses the `qaai/viewer` theme and its placeholder `str.replace()` render approach — no Jinja2 — so the catalog looks like the reviewer output viewers it sits beside. For the suite itself (markers, fixtures, how to run each tier) see the [Test guide](test_guide.html).
