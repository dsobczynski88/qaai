# Reviewer Caching Architecture

## Overview

All three reviewers (Test Suite / Test Case / Hazard) share a single **3-tier write-through
cache**, `ReviewCacheManager` (`autoqa/core/cache.py`). The goal is to eliminate redundant LLM
compute when the same entity is processed more than once — across iterative re-runs, partial
batch failures, or (for the hazard reviewer) the same requirement appearing as a risk-control
reference in multiple hazard rows.

On a cache hit, the node returns the previously stored result immediately, skipping the LLM
call. On a miss, the LLM runs and the result is written to cache before returning. Every cache
event (hit or miss) is recorded in `token_usage.jsonl` with the tokens and cost saved.

Cache entries are partitioned by an **entity id** — the requirement id (`REQ-*`), test-case id
(`TEST-*`), or hazard id (`HAZ-*`) — producing **one folder per entity directly under the cache
directory** (`./cache/` by default).

---

## Cache modes: `off` / `partial` / `full`

Per-run behaviour is controlled by `cache_mode`, threaded through the graph state
(`state["cache_mode"]`) from the API/service into every node. The UI's "Use cached results"
checkbox maps **checked → `partial`** and **unchecked → `off`**. `full` is internal-only.

| Node kind | `off` | `partial` (default) | `full` |
|-----------|-------|---------------------|--------|
| Interim node (decompose, summarize, per-spec eval, H1–H7, …) | no read/write | **read + write** | **read + write** |
| Final output node (`synthesizer` / `aggregator` / `final_assessment`) | no read/write | **write only — always re-runs** | **read + write** |

- **`partial`** reuses every intermediate result but always regenerates the final assessment,
  so a re-run is cheap yet produces a fresh top-level verdict/prose.
- **`full`** also serves the final node from cache. It is used internally for the hazard
  reviewer's embedded test-suite subgraph (see below); it is never sent from the UI.
- **`off`** bypasses the cache entirely (no reads, no writes).

The gating lives in `BaseLLMNode._cache_read_allowed(state)` / `_cache_write_allowed(state)`
(`autoqa/components/shared/nodes.py`). A node is marked final by constructing it with
`is_final_output=True` (done in the synthesizer/aggregator/final-assessor factories).

The global `ENABLE_CACHE` setting is a hard master switch: when `false`, no cache manager is
built and `cache_mode` is irrelevant.

---

## The three tiers

| Tier | Storage | TTL | Purpose |
|------|---------|-----|---------|
| 1 | LLM Provider | Provider-managed | Pass-through; handled by the API endpoint. |
| 2 | Redis (optional) | 24 hours | Hot in-memory cache. Gracefully disabled if Redis is unavailable or the `redis` package is missing. |
| 3 | Disk | Permanent | Persistent JSON files under `./cache/`. Survives restarts. Primary regulatory evidence artifact. |

**Read order on a check:** Redis → Disk → Miss. On a disk hit the entry is backfilled into
Redis. **Write order:** Disk first, then Redis. All cache I/O failures are swallowed (logged as
warnings) so a cache problem never breaks a review run.

---

## Cache keys and file layout

| Component | Source | Example |
|-----------|--------|---------|
| `entity_id` | `requirement.req_id`, `test_case.test_id`, or `hazard.hazard_id` | `REQ-PUMP-101`, `TEST-PUMP-201`, `HAZ-PUMP-001` |
| `node_name` | `self.__class__.__name__.lower()`, with `spec_id` appended for per-spec evaluators | `synthesizernode`, `singlespecevaluatornode_S1` |
| `prompt_version` | `ReviewCacheManager.extract_prompt_version(template_path)` | `v1.0.0`, `v8.0.0` |

**Redis key:** `review:{entity_id}:{node_name}:{prompt_version}`
**Disk path:** `{CACHE_DIR}/{safe_entity_id}/{safe_node_name}_{prompt_version}.json`

Both the folder (`entity_id`) and the filename token (`node_name`) are sanitised — non-word
characters replaced with `_` — so per-spec ids are filesystem-safe.

### Example layout after a hazard run referencing two requirements

```
./cache/
  HAZ-PUMP-001/
    hazardevaluatornode_h1_v1.0.0.json    … h2 … h3 … h7
    h6evaluatornode_v1.0.0.json
    hazarddesignsummarizernode_v1.0.0.json
    hazardneedssummarizernode_v1.0.0.json
    _finalassessornode_v1.0.0.json        (written; under `partial` it is re-run each time)
  REQ-PUMP-101/
    requirementreviewernode_v8.0.0.json   (the whole RTM subgraph result, "fully cached")
  REQ-PUMP-102/
    requirementreviewernode_v8.0.0.json
```

A **standalone** Test Suite run instead writes per-node files under `REQ-*` (e.g.
`decomposernode_v5.0.0.json`, `summarizernode_v4.0.0.json`,
`singlespecevaluatornode_S1_v7.0.0.json`, …). A Test Case run writes per-node files under
`TEST-*`.

---

## Node coverage

| Reviewer | Cached nodes | Entity | Final (re-run under `partial`) |
|----------|--------------|--------|-------------------------------|
| Test Suite | decomposer, summarizer, design_summarizer, per-spec `spec_evaluator`, synthesizer | `requirement.req_id` | `synthesizer` |
| Test Case | decomposer, per-spec `coverage_evaluator`, logical_evaluator, prereqs_evaluator, aggregator | `test_case.test_id` | `aggregator` |
| Hazard | H1–H7, design/needs summarizers, `requirement_reviewer` (RTM blob), final_assessment | `hazard.hazard_id` (req blob: `requirement.req_id`) | `final_assessment` |

---

## Hazard ⇄ embedded test-suite subgraph

The hazard reviewer embeds the full Test Suite reviewer as a subgraph, invoked once per traced
requirement by `RequirementReviewerNode`. That node caches the **entire** subgraph result as one
blob keyed on `req_id` (`requirementreviewernode_{synthesizer_version}.json`). A hit therefore
returns the complete review — including the synthesized assessment — without re-running anything,
i.e. the subgraph is **"fully cached"**.

To avoid double-caching, the embedded RTM is constructed **without** its own cache manager (its
internal nodes never self-cache); the blob is the subgraph's only cache. This is why the API
builds a *separate*, uncached embedded RTM for the hazard service rather than sharing the
cache-enabled RTM used by the standalone `/test-suite-review` endpoint.

### Cross-hazard requirement deduplication

Because the blob is keyed on `req_id` (not `hazard_id`), a requirement that appears as a
risk-control reference in several hazard rows is reviewed **once**; every later row hits the
cache. The `prompt_version` for this key comes from `PromptConfig.synthesizer`, so bumping the
synthesizer version busts requirement-review entries. Token savings are measured by snapshotting
the telemetry tracker's totals around the subgraph invocation.

---

## Cache file schema

```json
{
  "result": { "...model.model_dump()..." : null },
  "meta": {
    "entity_id": "HAZ-PUMP-001",
    "node": "hazardevaluatornode_h1",
    "prompt_version": "v1.0.0",
    "model": "gpt-4o-mini",
    "prompt_tokens": 1234,
    "completion_tokens": 456,
    "ts": "2026-05-25T10:30:00+00:00",
    "cache_tier_origin": 3
  }
}
```

`result` holds the raw `model_dump()` captured before `_format_response`. On restore the model
is reconstructed via `model_validate(cached["result"])` so LangGraph reducers receive
properly-typed Pydantic instances.

---

## Cache invalidation

Invalidation is **version-driven**: the version is parsed from the PromptConfig template path
(`hazard_h1/v1.0.0/template.jinja2 → v1.0.0`). Bumping a node's prompt version produces a new
key; old entries are simply never read again. No manual purge required.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_CACHE` | `true` | Master switch — set `false` to disable caching entirely |
| `CACHE_DIR` | `./cache` | Root directory for disk cache files (one folder per entity id) |
| `REDIS_URL` | `None` | Redis connection URL, e.g. `redis://localhost:6379`; unset disables Tier 2 |

A single `ReviewCacheManager` is built once in the API lifespan (`autoqa/api/main.py`) and shared
by all three services. It reuses the `TokenUsageTracker` already wired into the
`RateLimitOpenAIClient` so cache events land in the same `token_usage.jsonl` as LLM-call records.

---

## Telemetry — `token_usage.jsonl` events

**Cache hit:**
```json
{ "type": "cache_hit", "tier": 3, "node": "hazardevaluatornode_h1", "entity_id": "HAZ-PUMP-001",
  "tokens_saved_prompt": 1234, "tokens_saved_completion": 456, "tokens_saved_total": 1690,
  "cost_saved_usd": 0.000460, "model": "gpt-4o-mini" }
```

**Cache miss:**
```json
{ "type": "cache_miss", "node": "hazardevaluatornode_h1", "entity_id": "HAZ-PUMP-001" }
```

A `summary` record (cache hits redis/disk, misses, tokens/cost saved) is appended by
`log_summary()` and mirrored in the `autoqa.log` summary line.

---

## Adding caching to a new node

1. Construct the node with `cache_manager=` and
   `prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template)` (the `make_*`
   factories already do this). Pass `is_final_output=True` if it is the graph's final node.
2. Override `_get_cache_entity_id(self, state)` to return the entity id.
3. For a `StandardLLMNode` / `BatchedLLMNode` subclass, nothing else is needed — `__call__`
   gates read/write via `_cache_read_allowed` / `_cache_write_allowed` automatically.
4. For a node with a custom `__call__` (the per-spec evaluators, `_FinalAssessorNode`,
   `RequirementReviewerNode`), add the check/write blocks inline using the same helpers, and
   make sure any `Send` dispatcher that fans into the node copies `cache_mode` into each `Send`
   payload (otherwise the fan-out state defaults to `partial`).
