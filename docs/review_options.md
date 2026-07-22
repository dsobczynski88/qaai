# Review Options & Toggles

<div class="meta">QAAI (qaai) · qaai.api.schemas + qaai/web/src · generated from the codebase 2026-07-17</div>

Every QAAI review is shaped by the same small set of per-run options — the cache mode, test mode, and the three analysis toggles, plus the hazard upload's file inputs. This page is the single reference for *what each option does, what values it takes, its default, and which reviewer it affects*. It applies whether you call the API directly or drive the [Vue SPA](design/frontend_vue_rbac.html): **the SPA control and the API field are the same option** under two names.

## Overview

The two JSON endpoints (test-suite, test-case) take a `BaselineRequest` body; the hazard endpoint takes the same fields as multipart form parts alongside its file inputs <span class="src">qaai/api/schemas.py:6-64</span> <span class="src">qaai/api/routes.py:142-153</span>. In the SPA each field is rendered by a labelled control whose value is posted verbatim under the same field name <span class="src">qaai/web/src/components/forms/RtmForm.vue:26-43</span>. So the tables below carry **both** names: use the *API field* column when scripting `curl` and the *SPA control* column when clicking through the UI.

- **What to read where.** This page covers the options themselves. The [API guide](api.html) covers the endpoints, request bodies, and the async job model; the [Frontend &amp; RBAC design](design/frontend_vue_rbac.html) covers the SPA and how its controls are wired; the [Configuration guide](configuration.html) covers the *environment* settings (AI model, JAMA credentials, cache enable) that are set once per deployment rather than per run.
- **RBAC gates *who may run*, not the options.** The role model (admin / user) enables or disables the **Run** and **Feedback upload** actions; it never changes what an option means. Roles are enforced server-side too (`qaai/api/authz.py` — a direct API caller gets 401/403), with the SPA gating as a UX mirror <span class="src">qaai/web/src/constants.ts:15-20</span> <span class="src">qaai/api/authz.py:27</span>. See [Frontend &amp; RBAC → RBAC model](design/frontend_vue_rbac.html#rbac).

<h2 id="matrix">Every option at a glance</h2>

The *values / default* below are the request-model defaults <span class="src">qaai/api/schemas.py:6-64</span>; the SPA seeds its own initial values (noted in each row) which may differ from the API default. Each option links to its detail section.

<table>
<thead><tr><th>Option</th><th>API field</th><th>SPA control</th><th>Applies to</th><th>Default</th><th>Values</th></tr></thead>
<tbody>
<tr><td>Baseline</td><td><code>baseline_id</code></td><td>"JAMA Baseline ID"</td><td>test-suite, test-case</td><td><em>required</em></td><td>string, e.g. <code>BASE-84429</code></td></tr>
<tr><td><a href="#cache">Cache mode</a></td><td><code>cache_mode</code></td><td>"Cache mode" radio</td><td>all three</td><td><code>null</code> → <code>use_cache</code> (API); <code>on</code> (SPA)</td><td><code>off</code> / <code>on</code> / <code>test</code> (legacy <code>partial</code>/<code>full</code>)</td></tr>
<tr><td>Legacy cache bool</td><td><code>use_cache</code></td><td>— (no control)</td><td>all three</td><td><code>true</code></td><td>bool — <code>true</code>→<code>on</code>, <code>false</code>→<code>off</code></td></tr>
<tr><td><a href="#test-mode">Test mode</a></td><td><code>test_mode</code></td><td>"Test mode (cached JAMA only)"</td><td>all three</td><td><code>null</code> → <code>PYJAMA_TEST_MODE</code> (API); checked (SPA)</td><td>bool</td></tr>
<tr><td><a href="#edge-case">Edge case analysis</a></td><td><code>include_edge_case_analysis</code></td><td>"Include Edge Case Analysis"</td><td>test-suite, hazard</td><td><code>false</code></td><td>bool — <code>v4</code> vs <code>v3</code> prompt set</td></tr>
<tr><td><a href="#decomposition">Decomposition analysis</a></td><td><code>include_decomposition_analysis</code></td><td>"Include requirement decomposition analysis"</td><td>test-case only</td><td><code>true</code></td><td>bool — <code>v2</code> vs <code>v3</code> prompt set</td></tr>
<tr><td><a href="#design-summaries">Design summaries</a></td><td><code>include_design_summaries</code></td><td>"Include Design Summaries"</td><td>test-suite, hazard</td><td><code>false</code></td><td>bool — runs <code>design_summarizer</code></td></tr>
<tr><td><a href="#hazard-inputs">Project name</a></td><td><code>project_name</code></td><td>"Project Name"</td><td>hazard</td><td><em>required</em></td><td>string</td></tr>
<tr><td><a href="#hazard-inputs">SHA file</a></td><td><code>file</code></td><td>"SHA Excel Table"</td><td>hazard</td><td><em>required</em></td><td><code>.xlsx</code> / <code>.xls</code></td></tr>
<tr><td><a href="#hazard-inputs">Sheet name</a></td><td><code>sheet_name</code></td><td>"Sheet Name"</td><td>hazard</td><td><code>SHA Table</code></td><td>string</td></tr>
<tr><td><a href="#hazard-inputs">Requirements prefix</a></td><td><code>identifier_pattern</code></td><td>"Requirements Prefix"</td><td>hazard</td><td><code>GID-\d+</code></td><td>regex</td></tr>
</tbody></table>

Which of the three analysis toggles actually changes each reviewer:

<table>
<thead><tr><th>Toggle</th><th>Test Suite (RTM)</th><th>Test Case</th><th>Hazard</th></tr></thead>
<tbody>
<tr><td><code>include_edge_case_analysis</code></td><td>✓</td><td>— (ignored)</td><td>✓ (embedded RTM)</td></tr>
<tr><td><code>include_decomposition_analysis</code></td><td>— (ignored)</td><td>✓</td><td>— (ignored)</td></tr>
<tr><td><code>include_design_summaries</code></td><td>✓</td><td>— (ignored)</td><td>✓ (embedded RTM)</td></tr>
</tbody></table>

The hazard reviewer embeds the RTM reviewer as a subgraph for each traced requirement, so the two RTM-facing toggles apply to that embedded review; the hazard rubric's own H1–H6 dimensions are not otherwise affected <span class="src">qaai/api/routes.py:173,188</span>. Options a given endpoint does not use are simply ignored on that endpoint.

<h2 id="cache">Cache mode</h2>

`cache_mode` is the per-run cache policy, chosen in the SPA by the **cache-mode radio** <span class="src">qaai/web/src/components/controls/CacheModeRadio.vue:8-12</span>. When it is omitted from the request the server falls back to the legacy `use_cache` boolean (`true`→`on`, `false`→`off`), and the legacy radio values `partial`/`full` are still accepted and map to `on`/`test` <span class="src">qaai/api/routes.py:40-49</span> <span class="src">qaai/api/schemas.py:9-27</span>.

<table>
<thead><tr><th>Mode</th><th>SPA label</th><th>Reads cache?</th><th>Re-runs?</th><th>LLM calls</th></tr></thead>
<tbody>
<tr><td><code>on</code> <em>(default)</em></td><td>"On (reuse cached, fresh final)"</td><td>Newest cached result for every interim node</td><td>Only the final assessment re-runs</td><td>Final node + any cache misses</td></tr>
<tr><td><code>test</code></td><td>"Test (recreate from cache, no LLM)"</td><td>Every node, including the final</td><td>Nothing — served entirely from cache</td><td>None — a miss raises an error (surfaced as a <strong>400</strong>)</td></tr>
<tr><td><code>off</code></td><td>"Off (re-run all, save timestamped)"</td><td>Never reads</td><td>Every node re-runs</td><td>All nodes run live</td></tr>
</tbody></table>

<div class="note"><strong>How the modes behave in one line.</strong> "On" (default) reuses the newest
cached interim analysis and only re-runs the final assessment fresh, saving a new timestamped
result; "Test" recreates the report entirely from cached results with no LLM calls (and JAMA read
from cache), failing if any node result is missing; "Off" re-runs every node and saves a new
timestamped result, reusing nothing. Files are kept as immutable, timestamped history under
<code>./shared/runs</code>; a run that errors or comes out incomplete purges only the files it just
wrote <span class="src">qaai/web/src/constants.ts:24-27</span>. Selecting <code>test</code> also
forces <a href="#test-mode">test mode</a> so the JAMA fetch is cache-only. Full cache mechanics are
in <a href="configuration.html#caching">Configuration → Caching</a> and
<a href="design/caching.html">Caching design</a>.</div>

## Test mode

`test_mode` controls whether QAAI talks to JAMA live or reads JAMA results from the disk cache only <span class="src">qaai/api/schemas.py:28-35</span>. When it is `null` the server default `PYJAMA_TEST_MODE` applies; the SPA seeds the checkbox *on* <span class="src">qaai/web/src/components/forms/RtmForm.vue:16</span>.

<div class="note"><strong>Cached-JAMA-only.</strong> When test mode is on, QAAI runs strictly from
previously cached JAMA results — no live JAMA API calls are made, so invalid or mock credentials are
tolerated. Turn it off to fetch live from JAMA <span class="src">qaai/web/src/constants.ts:28-31</span>.
Selecting <a href="#cache">cache mode</a> <code>test</code> forces this on. JAMA credentials
themselves live in the environment, not in a request — see
<a href="configuration.html">Configuration</a>.</div>

<h2 id="edge-case">Edge case analysis</h2>

`include_edge_case_analysis` selects the prompt set used to decompose and review each requirement <span class="src">qaai/api/schemas.py:36-44</span> <span class="src">qaai/api/services.py:37-39</span>:

- **Off** *(default)* — the baseline set `test_suite_reviewer_v3`.
- **On** — the edge-case set `test_suite_reviewer_v4`, whose decomposer surfaces boundary, concurrency, state/mode, and degenerate-input specs.

It applies to the test-suite (RTM) reviewer and, on the hazard endpoint, to the embedded per-requirement RTM subgraph; the test-case reviewer ignores it. Cached results are namespaced by prompt set so `v3` and `v4` never alias <span class="src">qaai/web/src/constants.ts:32-35</span> <span class="src">qaai/api/routes.py:102,173</span>.

<h2 id="decomposition">Decomposition analysis</h2>

`include_decomposition_analysis` is the **test-case reviewer only** toggle <span class="src">qaai/api/schemas.py:45-54</span> <span class="src">qaai/api/services.py:42-44</span>:

- **On** *(default)* — `test_case_reviewer_v2`: decompose each requirement into atomic specs and evaluate coverage per spec.
- **Off** — `test_case_reviewer_v3`: skip decomposition and review each test case directly against the original requirement text — faster, coarser-grained.

Other endpoints ignore it <span class="src">qaai/web/src/constants.ts:36-37</span>.

## Design summaries

`include_design_summaries` toggles the `design_summarizer` node in the test-suite (RTM) graph <span class="src">qaai/api/schemas.py:55-64</span>. A gate router adds the node only when the flag is set <span class="src">qaai/agents/test_suite_reviewer/nodes.py:59-72</span>:

- **On** — runs `design_summarizer` so summarized design documents feed per-spec coverage and the R6 Design Alignment criterion in synthesis <span class="src">qaai/agents/test_suite_reviewer/nodes.py:452-485</span>.
- **Off** *(default)* — that branch is skipped in the graph.

It applies to the test-suite reviewer and the hazard endpoint's embedded RTM subgraph. Cached results for design-sensitive nodes are keyed by this flag (a `ds0`/`ds1` discriminator) so switching it never reuses a result computed under the other mode.

<div class="note"><strong>Hazard note.</strong> On the hazard endpoint this only affects the embedded
per-requirement test-suite review; it does <em>not</em> change the hazard rubric's own H2/H3 design
analysis <span class="src">qaai/web/src/constants.ts:38-41</span>.</div>

<h2 id="hazard-inputs">Hazard upload inputs</h2>

The hazard endpoint has no `baseline_id`; instead it takes an uploaded SHA workbook and the parameters needed to read it, as multipart form parts <span class="src">qaai/api/routes.py:142-153</span>:

<table>
<thead><tr><th>Field</th><th>SPA control</th><th>Required</th><th>Default</th><th>Notes</th></tr></thead>
<tbody>
<tr><td><code>project_name</code></td><td>"Project Name"</td><td>Yes</td><td>—</td><td>Drives the JAMA <code>bidirectional_trace</code> fetch that resolves each row's requirement references</td></tr>
<tr><td><code>file</code></td><td>"SHA Excel Table"</td><td>Yes</td><td>—</td><td>The SHA hazard table (<code>.xlsx</code>/<code>.xls</code>); other types → <strong>400</strong>. One review per row</td></tr>
<tr><td><code>sheet_name</code></td><td>"Sheet Name"</td><td>No</td><td><code>SHA Table</code></td><td>Worksheet holding the hazard table</td></tr>
<tr><td><code>identifier_pattern</code></td><td>"Requirements Prefix"</td><td>No</td><td><code>GID-\d+</code></td><td>Regex extracting the control/requirement IDs from the Risk Control Measures column (passed to the loader as <code>extract_gids_format</code>)</td></tr>
</tbody></table>

<div class="note warn"><strong>The Excel is not self-contained.</strong> The workbook supplies only
the hazard-register fields and the requirement <em>ID references</em>; the requirement text, test
cases, and design docs the review evaluates are fetched from JAMA against those IDs. The hazard
reviewer therefore needs live JAMA credentials or cached JAMA results (<a href="#test-mode">test
mode</a>) to run correctly — see <a href="api.html#hazard">API guide → Hazard upload</a> for the full
explanation <span class="src">qaai/api/services.py:503-515,586-594</span>.</div>
