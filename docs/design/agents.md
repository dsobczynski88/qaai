# Reviewer Agent Design

<div class="meta">QAAI (qaai) · generated from the codebase 2026-07-06</div>

## Overview

QAAI implements three independent LangGraph reviewers, each a compiled async pipeline that emits a structured, SoP-gating rubric with a binary Yes/No verdict:

<table>
<thead><tr><th>Reviewer</th><th>Package</th><th>Rubric</th><th>Output model</th></tr></thead>
<tbody>
<tr><td>Test Suite (RTM)</td><td><code>qaai.agents.test_suite_reviewer</code></td><td>M1–M5 + R6</td><td><code>SynthesizedAssessment</code></td></tr>
<tr><td>Test Case</td><td><code>qaai.agents.test_case_reviewer</code></td><td>5 review objectives</td><td><code>TestCaseAssessment</code></td></tr>
<tr><td>Hazard Risk</td><td><code>qaai.agents.hazard_risk_reviewer</code></td><td>H1–H6 + R7</td><td><code>HazardAssessment</code></td></tr>
</tbody></table>

The hazard reviewer **embeds the full RTM reviewer as a subgraph**: each requirement traced from a hazard record is reviewed by invoking the RTM graph.

<h2 id="layout">The canonical four-file layout</h2>

Every reviewer lives under `qaai/agents/<reviewer>/` with the same four files:

<table>
<thead><tr><th>File</th><th>Responsibility</th></tr></thead>
<tbody>
<tr><td><code>core.py</code></td><td>Pydantic output models (one per node) <em>and</em> the <code>TypedDict</code> graph state. Parallel fan-in fields use <code>Annotated[List[X], operator.add]</code> so <code>Send</code> results concatenate.</td></tr>
<tr><td><code>nodes.py</code></td><td>Node classes (subclasses of the shared base nodes), paired <code>make_*_node(...)</code> factories, and <code>dispatch_*(state) -&gt; List[Send]</code> fan-out functions (which return <code>[]</code> on invalid state).</td></tr>
<tr><td><code>pipeline.py</code></td><td>A <code>*Runnable</code> class whose <code>build()</code> constructs the <code>StateGraph</code>, wires edges, compiles with an optional checkpointer, and renders <code>graph.png</code>.</td></tr>
<tr><td><code>__init__.py</code></td><td>Exports the Runnable. (Hazard adds <code>loader.py</code> for parsing the SHA Excel.)</td></tr>
</tbody></table>

<h2 id="rtm">Test Suite Reviewer (RTM)</h2>

State: `RTMReviewState` with fan-in field `coverage_analysis: Annotated[List[EvaluatedSpec], operator.add]` <span class="src">test_suite_reviewer/core.py:241</span>. Built by `RTMReviewerRunnable.build()` <span class="src">test_suite_reviewer/pipeline.py:73-181</span>:

<pre class="diagram"><code>START
  -&gt; data_integration            (JAMA fetch, or no-op when data already in state)
  -&gt; transform                   (JAMA rows -&gt; graph state)
  -&gt; validation_gate             (skip the graph -&gt; END when required inputs are missing)
  -&gt; [ decomposer | summarizer | design_summarizer ]   (parallel)
  -&gt; coverage_router             (join)
  -&gt; dispatch_coverage  --Send xN--&gt;  spec_evaluator    (parallel, one per decomposed spec)
  -&gt; synthesizer                 (reduces coverage_analysis via operator.add)
  -&gt; END</code></pre>

Nodes: `SummaryNode` / `DesignSummarizerNode` (`BatchedLLMNode`), `SingleSpecEvaluatorNode`, and the `SynthesizerNode` (marked `is_final_output=True`) plus the shared `DecomposerNode`. `dispatch_coverage` fans out one `Send` per decomposed spec and copies `cache_mode` into each payload <span class="src">test_suite_reviewer/nodes.py:164-190</span>.

<h2 id="tc">Test Case Reviewer</h2>

State: `TCReviewState`; in decomposition mode the requirement axis fans out per requirement to a fused decompose→coverage node, with both `decomposed_requirements` and `coverage_analysis` as `Annotated[List[...], operator.add]` channels, while the logical and prereqs axes are single test-case-level nodes <span class="src">test_case_reviewer/core.py</span>. Built by `TCReviewerRunnable.build()` <span class="src">test_case_reviewer/pipeline.py</span>:

<pre class="diagram"><code>START
  -&gt; data_integration -&gt; transform -&gt; validation_gate
  -&gt; coverage_router
       |-- dispatch_requirement_pipeline --Send xN--&gt; requirement_pipeline
       |        (decompose one requirement, then cover its specs concurrently)
       |-- (direct edge) ---------------------------&gt;  logical_evaluator
       |-- (direct edge) ---------------------------&gt;  prereqs_evaluator
  -&gt; aggregator                   (5-row evaluated_checklist)
  -&gt; END</code></pre>

The five review objectives are embedded directly in the `single_test_aggregator` prompt (v8/v9).

<h2 id="hazard">Hazard Risk Reviewer</h2>

State: `HazardReviewState` with two fan-in fields — `requirement_reviews` and `hazard_findings` (both `operator.add`) <span class="src">hazard_risk_reviewer/core.py:408,411</span>. The graph is staged by data dependency <span class="src">hazard_risk_reviewer/pipeline.py:144-315</span>:

<pre class="diagram"><code>START -&gt; data_integration -&gt; transform -&gt; validation_gate
  validation_gate            (skip the graph -&gt; END when required SHA fields are missing)
  -&gt; work_router             (fan-out hub for the early work)
       |-- dispatch_early --Send--&gt; [ h1 | r7 ]           (need only hazard fields)
       |-- dispatch_reviews --Send xN--&gt; requirement_reviewer   (each runs the RTM SUBGRAPH)
       |-- design_summarizer , needs_summarizer
  design_summarizer --Send--&gt; [ h2 | h3 ]                (consume summarized_designs)
  [ requirement_reviewer | design_summarizer | needs_summarizer ]
  -&gt; late_evaluator_router
  -&gt; dispatch_late --Send--&gt; [ h4 | h5 ]
  -&gt; h6                       (joins after H4/H5; H3 reaches it via the reducer)
  -&gt; final_assessment         (deterministic; waits on h1,h2,h6,r7)
  -&gt; END</code></pre>

The `final_assessment` node computes `overall_verdict` **deterministically** from the seven Yes/No/N-A findings (never by the LLM). `RequirementReviewerNode` wraps a shared `RTMReviewerRunnable` and invokes `await self.rtm.graph.ainvoke(rtm_input)` for one requirement, caching the whole-subgraph result as one `req_id`-keyed blob <span class="src">hazard_risk_reviewer/nodes.py:313-446</span>.

<h2 id="nodes">Shared node engine</h2>

All nodes derive from base classes in `qaai/agents/shared/nodes.py`. `StandardLLMNode` is a **Template Method**: its `__call__` runs a fixed pipeline and subclasses fill in only the hooks <span class="src">shared/nodes.py:547-613</span>:

<pre class="diagram"><code>__call__(state):
  _validate_state(state)         -&gt; False ? return _get_skip_response()  (soft-fail)
  cache read (if allowed)        -&gt; hit ? return _format_response(restored)
  _build_payload(state)          -&gt; messages
  client.chat_completion(...)    -&gt; raw LLM output
  _parse_llm_response(...)       -&gt; Pydantic model (JSON extraction + validation)
  cache write (if allowed)
  _format_response(parsed)       -&gt; state delta</code></pre>

`BatchedLLMNode` fans multiple items in parallel via `asyncio.gather`. JSON extraction handles markdown fences, bracket balancing, and Llama-style missing delimiters — node code never hand-parses LLM JSON. **Soft-fail** is the rule: a failed validate/parse returns an empty/skip response rather than raising, so a missing upstream field shows up as a node that "skips" in the logs, not a crash.

<h2 id="cache">Cache manager system</h2>

`ReviewCacheManager` is a write-through cache on disk, shared by all reviewers <span class="src">qaai/core/cache.py</span>: each per-node result is persisted as an append-only, timestamped JSON file at `{cache_dir}/{entity_id}/[{prompt_set}/]{node}_{prompt_version}_{timestamp}.json` (reads select the newest), keyed `review:{entity_id}:[{prompt_set}:]{node}:{prompt_version}`. See the [Caching design doc](caching.html) for the full layout and payload schema.

Caching is governed per node by `cache_mode` threaded through state <span class="src">shared/nodes.py:169-192</span>: `off` never reads but re-runs every node and still writes a new timestamped result; `on` (default) reuses cached interim nodes but always re-runs nodes flagged `is_final_output=True`; `test` reads all nodes (incl. final) and makes no LLM calls (a miss raises `CacheRequiredError`). Because `Send` creates fresh payloads, every dispatcher copies `cache_mode` into each fan-out payload so children inherit the policy. Bumping a prompt version changes the key — the invalidation mechanism. See [Configuration → Caching](../configuration.html#caching).

<h2 id="logging">Logging &amp; telemetry</h2>

Each run creates a timestamped folder `logs/run-<timestamp>/` via `start_new_run()` <span class="src">qaai/core/logging_config.py</span>, holding `qaai.log` (and `api.log` / `pyjama.log` for the server). The `TokenUsageTracker` appends one record per LLM call — and cache hit/miss events — to `token_usage.jsonl`, with a cost computed from the `TOKEN_COST_*` rates, plus a summary record at teardown <span class="src">qaai/core/telemetry.py</span>.

<h2 id="viewers">Output viewer files</h2>

At the end of a run, the `qaai.viewer` package renders a self-contained HTML report from `outputs.jsonl`: `write_viewer` → `viewer.html` (RTM), `write_viewer_tc` → `viewer_tc.html`, `write_viewer_hz` → `viewer_hz.html` <span class="src">qaai/viewer/generator.py:83-135</span>. Each template is assembled modularly from `viewer/common/` (shared `base.css`, `layout.html`, `shared.js`) plus the reviewer's own `style.css` / `script.js`, producing a single static file with no external dependencies (reviewer ratings persist in `localStorage`).

<h2 id="prompts">How prompts are created</h2>

Prompts are a versioned registry, not flat files. Each node role maps (via `PromptConfig`) to a template at `qaai/prompts/<role>/<version>/template.jinja2` with a `meta.yaml` sidecar; `make_*_node` factories render the template with `render_prompt(path, **vars)` and extract the version for the cache key. Named **prompt sets** (manifests in `qaai/prompts/sets/`) pin a version per role so a whole bundle can be swapped via the `PROMPT_SET` env var — see [Configuration → Prompt sets](../configuration.html#promptsets).

<h2 id="patterns">Design patterns at a glance</h2>

<table>
<thead><tr><th>Pattern</th><th>Where</th><th>Why</th></tr></thead>
<tbody>
<tr><td>Template Method</td><td><code>StandardLLMNode.__call__</code> <span class="src">shared/nodes.py:547-613</span></td><td>One orchestration; nodes implement only payload/format hooks.</td></tr>
<tr><td>Send fan-out + <code>operator.add</code> reduce</td><td><code>dispatch_*</code> + <code>Annotated[List, operator.add]</code></td><td>Maximize parallelism; results concatenate deterministically.</td></tr>
<tr><td>Subgraph embedding</td><td><code>RequirementReviewerNode</code> <span class="src">hazard_risk_reviewer/nodes.py:313-446</span></td><td>Reuse the entire RTM pipeline per traced requirement.</td></tr>
<tr><td>Soft-fail nodes</td><td><code>_validate_state</code> / dispatchers returning <code>[]</code></td><td>Missing upstream data degrades to a skip, not a crash.</td></tr>
<tr><td>Factory functions</td><td><code>make_*_node(...)</code></td><td>Inject client/model/prompt + cache wiring consistently.</td></tr>
<tr><td>Write-through cache</td><td><code>ReviewCacheManager</code> <span class="src">core/cache.py</span></td><td>Durable disk JSON as regulatory evidence; prompt-version-keyed invalidation.</td></tr>
<tr><td>Versioned prompt registry</td><td><code>qaai/prompts</code> + <code>_registry.py</code></td><td>Reproducible prompts; version is the cache-invalidation key.</td></tr>
</tbody></table>
