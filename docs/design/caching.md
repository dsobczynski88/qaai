# Caching

<div class="meta">QAAI (qaai) · the shared review cache, on disk</div>

## Overview

A single write-through `ReviewCacheManager` <span class="src">qaai/core/cache.py</span> backs all three reviewers (Test Suite, Test Case, Hazard Risk). It serves two goals at once:

- **Cost &amp; latency.** Re-running a review reuses prior per-node LLM results, so a second run only pays for the nodes whose inputs (or prompt versions) actually changed.
- **Regulatory evidence.** Every cached node result is a durable JSON file on disk, partitioned by the entity (requirement / test case / hazard) it belongs to — an auditable record of what the model produced for each artifact.

The manager is created once at app startup and shared across every service and graph <span class="src">qaai/api/main.py</span>. Each LLM node opts into caching by exposing an entity id (see [Cache keys](#keys)); nodes that do not expose one simply run uncached.

<div class="note">The manager is write-through over up to three tiers: an optional
<strong>Redis</strong> layer (Tier 2, enabled only when <code>REDIS_URL</code> is set;
holds the latest write under a timestamp-free key with a 24h TTL and degrades gracefully when
absent) in front of the durable <strong>Disk</strong> layer (Tier 3), which is the
regulatory system of record. This page describes the Disk layer; Redis is a transparent
read-through accelerator over it.</div>

<h2 id="disk">The Disk cache</h2>

The cache persists to plain JSON files on disk — the layer historically called *"Tier 3"*, referred to throughout this document simply as **Disk**. It is the single durable store: it survives process restarts and is the artifact a reviewer or auditor inspects after a run.

Files are laid out one folder per entity directly under the cache root (`CACHE_DIR`, default `./shared/runs`):

<pre class="diagram"><code>{CACHE_DIR}/{entity_id}/{node}_{prompt_version}_{timestamp}.json</code></pre>

Files are **append-only and immutable**: every write creates a new file whose name carries a timestamp (`%Y_%m_%d_%H_%M_%S_%f`), and a read selects the **newest** matching file. A full history of what the model produced for each artifact is therefore preserved rather than overwritten <span class="src">qaai/core/cache.py</span>.

The `entity_id` folder name carries a reviewer-specific prefix, so all three reviewers coexist under one cache root:

<table>
<thead><tr><th>Prefix</th><th>Reviewer</th><th>Example folder</th></tr></thead>
<tbody>
<tr><td><code>REQ-*</code></td><td>Test Suite (RTM)</td><td><code>./shared/runs/REQ-PUMP-101/</code></td></tr>
<tr><td><code>TEST-*</code></td><td>Test Case</td><td><code>./shared/runs/TEST-4417/</code></td></tr>
<tr><td><code>HAZ-*</code></td><td>Hazard Risk</td><td><code>./shared/runs/HAZ-12/</code></td></tr>
</tbody></table>

Each file stores both the model result and a metadata sidecar in one payload, so the evidence is self-describing <span class="src">qaai/core/cache.py</span>:

```
{
  "result": { ...the node's Pydantic model_dump()... },
  "meta": {
    "entity_id":        "REQ-PUMP-101",
    "node":             "synthesizer",
    "prompt_version":   "v8.0.0",
    "prompt_set":       "test_suite_reviewer_v3",   // null when un-namespaced
    "model":            "gpt-4o-mini",
    "prompt_tokens":     1234,
    "completion_tokens": 567,
    "ts":               "2026-07-06T09:00:00Z"
  }
}
```

<div class="note">Entity ids and node names are sanitized to filesystem-safe characters
(alphanumerics, hyphen, underscore) before they become folder / file names, so an id with
slashes or spaces still maps to a single predictable path.</div>

<h2 id="keys">Cache keys</h2>

A cache entry is addressed by three coordinates — **entity**, **node**, and **prompt version**. They form the Redis key and the on-disk filename *prefix* (the trailing `_{timestamp}` distinguishes the append-only writes; the newest file for a prefix wins):

<pre class="diagram"><code>review:{entity_id}:{node_name}:{prompt_version}          # Redis key
{entity_id}/{node_name}_{prompt_version}_{timestamp}.json  # Disk file</code></pre>

- `entity_id` — the artifact under review (`REQ-*` / `TEST-*` / `HAZ-*`). Returned per node by `_get_cache_entity_id(state)`; a node that returns `None` is not cached <span class="src">qaai/agents/shared/nodes.py</span>.
- `node_name` — which logical node produced the result (e.g. `decomposer`, `coverage`, `synthesizer`). Defaults to the node class name; nodes that share one class across several graph positions override `_get_cache_node_name()` so each gets a distinct key.
- `prompt_version` — the semver of the Jinja2 template the node rendered, parsed from its path (e.g. `coverage_evaluator/v8.0.0/template.jinja2` → `v8.0.0`). This is what makes the cache self-invalidating (see [Invalidation](#invalidation)).

When a run uses a named prompt set, the set name is folded into the key (and the disk path) — see [Prompt-set namespacing](#promptsets).

<h2 id="modes">Per-run cache modes</h2>

Caching is controlled per run by a `cache_mode` carried on graph state, read by every node's read/write gates <span class="src">qaai/agents/shared/nodes.py</span>:

<table>
<thead><tr><th>Mode</th><th>Reads</th><th>Writes</th><th>Behavior</th></tr></thead>
<tbody>
<tr><td><code>off</code></td><td>—</td><td>all</td><td>Never reads. Re-runs every node, but <strong>still writes a new timestamped file</strong> for each — nothing is reused, yet the run's results are preserved as history.</td></tr>
<tr><td><code>on</code> <em>(default)</em></td><td>interim only</td><td>all</td><td>Reuse the newest cached result for every interim node, but <strong>always re-run the graph's final node</strong> (synthesizer / aggregator / final assessment, flagged <code>is_final_output=True</code>) so the top-level verdict is always freshly produced. Writes through.</td></tr>
<tr><td><code>test</code></td><td>all</td><td>—</td><td>Read the newest cached result for <strong>all</strong> nodes, including the final one, and make <strong>no LLM calls</strong> — a cache miss raises <code>CacheRequiredError</code> (surfaced as HTTP 400). Used to regenerate a report entirely from prior evidence, fully offline.</td></tr>
</tbody></table>

The gating is small and lives entirely in the base node <span class="src">qaai/agents/shared/nodes.py</span>:

```
@staticmethod
def _mode(state):
    return (state or {}).get("cache_mode", "on")

def _cache_read_allowed(self, state):
    if self.cache_manager is None or not self.prompt_version:
        return False
    mode = self._mode(state)
    if mode == "off":
        return False
    if mode == "on" and self.is_final_output:   # final node always re-runs
        return False
    return True  # "on" (interim) and "test" (all nodes) may read

def _cache_write_allowed(self, state):
    if self.cache_manager is None or not self.prompt_version:
        return False
    return self._mode(state) in ("off", "on")   # "test" never writes
```

<div class="note">The API exposes a per-endpoint <strong>cache-mode radio</strong>
(<code>on</code> "reuse cached, fresh final" · <code>test</code> "recreate from cache, no LLM" ·
<code>off</code> "re-run all, save timestamped"), resolved by <code>_resolve_cache_mode()</code>
<span class="src">qaai/api/routes.py</span>. The legacy <code>use_cache</code> bool still maps
<code>true</code> → <code>on</code> / <code>false</code> → <code>off</code>,
and the legacy radio values <code>partial</code> → <code>on</code> /
<code>full</code> → <code>test</code> are still accepted. The hazard reviewer's
embedded RTM subgraph inherits the parent run's <code>cache_mode</code> unchanged
(see <a href="#reqblob">the per-requirement blob cache</a>) — it is never forced
into a different mode internally.</div>

<h2 id="concurrency">Concurrency</h2>

Within one review job, items run under a <strong>per-reviewer</strong> concurrency cap. `_run_batch_review` <span class="src">qaai/api/services.py:233-298</span> fans every item out with `asyncio.gather` under an `asyncio.Semaphore` sized by that reviewer's knob — `TEST_SUITE_MAX_CONCURRENT_REVIEWS` (8), `TEST_CASE_MAX_CONCURRENT_REVIEWS` (8), or `HAZARD_MAX_CONCURRENT_REVIEWS` (<strong>1</strong>) — a soft cap that sits above the client's hard RPM/TPM limiter. The hazard reviewer defaults to <strong>1</strong>: because this cache is write-after-completion with no in-flight dedup, running hazard records sequentially lets the first record warm the shared per-doc design summaries (`DD-*`) and per-requirement RTM result blobs (`REQ-*`) before the next record starts, instead of many records recomputing the same shared entities at once. Each record still fans its own summarizers and requirement reviews out concurrently — the embedded RTM subgraph fan-out (in `RequirementReviewerNode`) is itself bounded by `TEST_SUITE_MAX_CONCURRENT_REVIEWS`, and cache hits bypass that semaphore so only live subgraph runs count against it. Each item still fans out internally via `Send`, so peak concurrent LLM calls are roughly the semaphore width times the per-graph fan-out. `return_exceptions=True` means one item's exception never cancels its siblings; a raised or incomplete item has only its own run purged (see <a href="#invalidation">Invalidation</a>) rather than aborting the batch, while a `test`-mode cache miss on <em>any</em> item is still a hard, whole-job failure. Outputs are written back to `outputs.jsonl` in original input order regardless of completion order.

<h2 id="threading">Threading &amp; fan-out</h2>

`cache_mode` is a first-class field on the shared graph state (`BaseReviewState`) <span class="src">qaai/agents/shared/core.py</span>; the API/service sets it once and every node reads it from state. The one place this needs care is the `Send` fan-out: when a dispatcher fans work out to parallel nodes, each `Send` payload is a fresh partial state, so the dispatcher must copy `cache_mode` into every payload or the fan-out branches silently fall back to the default <span class="src">qaai/agents/&lt;reviewer&gt;/nodes.py</span>:

```
cache_mode = state.get("cache_mode", "on")
return [
    Send("h1_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
    Send("r7_evaluator", {"hazard": hazard, "cache_mode": cache_mode}),
    ...
]
```

<h2 id="promptsets">Prompt-set namespacing</h2>

A run may resolve its prompts from a named **prompt set** (e.g. the "Include Edge Case Analysis" toggle selects `test_suite_reviewer_v3` vs `_v4`). Two sets can pin the *same* version for a node — so their keys would collide — yet they must not share cached results. To keep them separate, the set name is folded into both the key and the disk path when present:

<table>
<thead><tr><th></th><th>Key</th><th>Disk path</th></tr></thead>
<tbody>
<tr><td>Default (no set)</td><td><code>review:{entity_id}:{node}:{version}</code></td><td><code>{CACHE_DIR}/{entity_id}/{node}_{version}_{ts}.json</code></td></tr>
<tr><td>Named set</td><td><code>review:{entity_id}:{prompt_set}:{node}:{version}</code></td><td><code>{CACHE_DIR}/{entity_id}/{prompt_set}/{node}_{version}_{ts}.json</code></td></tr>
</tbody></table>

The set name flows `PromptConfig.set_name` → node constructor (`prompt_set`) → `ReviewCacheManager.get/set` <span class="src">qaai/core/config.py, qaai/core/cache.py</span>. It is optional and defaults to `None`, so default-config runs (e.g. the test-case reviewer) keep the legacy un-namespaced layout unchanged. This is exactly why `test_suite_reviewer_v3` and `_v4` never alias even though they share every node version except the decomposer.

<h2 id="peritem">Per-item design-doc summaries</h2>

Design-doc summarization is cached **per document, not per requirement/hazard**. `BatchedLLMNode` exposes an opt-in `PER_ITEM_CACHE` flag <span class="src">qaai/agents/shared/nodes.py:674-680</span>: when a subclass sets it, each item in the batch is looked up and written under its **own** cache entity — `_get_item_cache_id(item)` — instead of the requirement/hazard entity the node was invoked for. `__call__` then only LLM-processes the cache misses and merges hits back in original item order.

Both design summarizers opt in — the RTM `DesignSummarizerNode` <span class="src">qaai/agents/test_suite_reviewer/nodes.py:133-147</span> and the hazard `HazardDesignSummarizerNode` <span class="src">qaai/agents/hazard_risk_reviewer/nodes.py:85-105</span> — keyed by `doc_id` (typically a `DD-*` id). Each also overrides `_item_cache_prompt_set()` to return `None`, so a summary is written **un-namespaced** and reused across every prompt set, not just the one that first produced it — a design doc's content doesn't change with the decomposer version.

<div class="note">Because the hazard reviewer's embedded RTM subgraph shares the parent's
<code>cache_manager</code> (see below), a design doc cited by both a standalone RTM run and
a hazard's per-requirement subgraph run is summarized <strong>once</strong> and reused by
both — the per-item cache lives one level below the entity-scoped cache described above.</div>

<h2 id="dsdiscriminator">Design-summary discriminator (ds0/ds1)</h2>

A node whose output *depends on whether design summaries were included at all* (not just what they say) is marked `design_sensitive=True` at construction <span class="src">qaai/agents/shared/nodes.py:150,165-194</span>. Such a node's cache key/disk filename carries an extra `ds0`/`ds1` segment sourced from the per-request `include_design_summaries` toggle, so a run with the toggle on never accidentally reads a cached result produced with it off (or vice versa). This is separate from the prompt-set namespacing above and applies independently of it.

The hazard reviewer's `RequirementReviewerNode` blob cache (below) is design-sensitive for the same reason: the blob wraps the *entire* embedded RTM result, and that result's shape changes when `include_design_summaries` flips.

<h2 id="reqblob">The hazard reviewer's per-requirement blob cache</h2>

`RequirementReviewerNode` — the hazard graph's wrapper around the embedded RTM subgraph — caches the **whole RTM result** for a requirement under `req_id`, node name `requirementreviewernode` <span class="src">qaai/agents/hazard_risk_reviewer/nodes.py:317-405</span>. On a hit it returns the cached `RequirementReview` directly and **never invokes the RTM subgraph at all** for that requirement — the subgraph's own node-level caching (including the design-doc per-item cache above) only comes into play on a miss. The same requirement is frequently traced from multiple hazard rows, so this blob cache also means the RTM review for a shared requirement runs at most once per run, not once per hazard.

The embedded RTM subgraph is invoked with the **same `cache_mode` the parent hazard run received** — it is not forced into `test` mode internally <span class="src">qaai/agents/hazard_risk_reviewer/nodes.py:354,402</span>; a hazard run in `on` mode drives an embedded RTM run also in `on` mode, reusing the RTM subgraph's interim nodes and only re-running its final synthesizer.

## Invalidation

There is **no TTL or expiry** on disk entries (the optional Redis layer holds a 24h TTL copy) — disk invalidation is **version-driven**. Because the prompt version is part of the key, bumping a template's version (a new `qaai/prompts/<role>/v.../` directory) produces a new key automatically; the next run misses and recomputes, while the prior version's file remains on disk untouched as historical evidence. No manual purge is needed, and switching prompt sets has the same effect. Even at a fixed version, each run appends a new timestamped file, so the newest write wins while the history is preserved.

<div class="note">Keeping superseded versions on disk is deliberate: the cache doubles as a
regulatory artifact, so old results are retained rather than overwritten. Because writes are
per-node and write-through, the batch service reuses results only <strong>after</strong> the
fact: an item whose graph run fails or whose final state is incomplete has <em>only that run's</em>
files purged via <code>ReviewCacheManager.purge_run(entity_id, since, prompt_set)</code>
(run-scoped by timestamp, so earlier good runs survive)
<span class="src">qaai/api/services.py, qaai/core/cache.py</span>. To reclaim space, delete
entity folders (or per-version files) directly.</div>

<h2 id="enable">Enabling &amp; disabling</h2>

Two settings govern the cache globally <span class="src">qaai/core/config.py</span>:

<table>
<thead><tr><th>Setting</th><th>Default</th><th>Effect</th></tr></thead>
<tbody>
<tr><td><code>ENABLE_CACHE</code></td><td><code>true</code></td><td>Master switch. When <code>false</code>, no cache manager is created and every run recomputes regardless of the per-request toggle.</td></tr>
<tr><td><code>CACHE_DIR</code></td><td><code>./shared/runs</code></td><td>Root directory for the on-disk cache (one folder per entity).</td></tr>
<tr><td><code>REDIS_URL</code></td><td><code>None</code></td><td>Optional. When set, enables the Tier 2 Redis accelerator (24h TTL); when unset the cache is disk-only.</td></tr>
</tbody></table>

Per request, the cache-mode radio chooses `on` / `test` / `off` (the legacy `use_cache` toggle maps to `on` / `off`) for that run. See the [Configuration guide](../configuration.html#caching) for the env-var reference.

## Telemetry

Cache activity is observable: the `TokenUsageTracker` <span class="src">qaai/core/telemetry.py</span> records cache hit and miss events alongside token/cost records to `token_usage.jsonl` in the run directory, including the tokens a hit saved. The teardown summary reports cache effectiveness for the run, so you can see how much a cached re-run avoided recomputing.
