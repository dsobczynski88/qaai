# Hazard Reviewer Caching Architecture

## Overview

The hazard risk reviewer implements a **3-tier write-through cache** across all 10 LLM nodes in the pipeline. The goal is to eliminate redundant LLM compute when the same hazard or requirement is processed more than once — whether due to partial batch failures, iterative re-runs, or (crucially) the same requirement appearing as a risk-control reference in multiple hazard rows.

On a cache hit, the node returns the previously stored result immediately, skipping the LLM call entirely. On a miss, the LLM runs as normal and the result is written to cache before returning. Every cache event (hit or miss) is recorded in `token_usage.jsonl` with the tokens saved and estimated cost saved.

---

## The Three Tiers

| Tier | Storage | TTL | Purpose |
|------|---------|-----|---------|
| 1 | LLM Provider | Provider-managed | Pass-through; handled transparently by the API endpoint. No action taken by autoqa. |
| 2 | Redis (optional) | 24 hours | Hot in-memory cache. Gracefully disabled if Redis is unavailable or the `redis` package is not installed. |
| 3 | Disk | Permanent | Persistent JSON files under `./cache/hazard/`. Survives restarts. Primary evidence artifact for regulatory review. |

**Read order on a cache check:** Redis → Disk → Miss.

When a disk hit occurs, the entry is backfilled into Redis so that the next access within the TTL window is served from memory.

**Write order on an LLM result:** Disk first, then Redis. Both writes are swallowed on failure (warnings logged) so a cache write failure never breaks a review run.

---

## Cache Keys and File Layout

Every cache entry is identified by three components:

| Component | Source | Example |
|-----------|--------|---------|
| `entity_id` | `hazard.hazard_id` (H1-H7, summarizers, final) or `requirement.req_id` (requirement reviewer) | `GID-1234`, `REQ-001` |
| `node_name` | `self.__class__.__name__.lower()` | `h1evaluatornode`, `requirementreviewernode` |
| `prompt_version` | Extracted from the PromptConfig template path at construction time | `v1.0.0` |

**Redis key:** `hazard:{entity_id}:{node_name}:{prompt_version}`

**Disk path:** `{HAZARD_CACHE_DIR}/{safe_entity_id}/{node_name}_{prompt_version}.json`

The `entity_id` is sanitised (non-word characters replaced with `_`) before use in a file path.

### Example disk layout after one hazard + two requirements

```
./cache/hazard/
  GID-1234/
    h1evaluatornode_v1.0.0.json
    h2evaluatornode_v1.0.0.json
    h3evaluatornode_v1.0.0.json
    h4evaluatornode_v1.0.0.json
    h5evaluatornode_v1.0.0.json
    h6evaluatornode_v1.0.0.json
    h7evaluatornode_v1.0.0.json
    hazarddesignsummarizernode_v1.0.0.json
    hazardneedssummarizernode_v1.0.0.json
    _finalassessornode_v1.0.0.json
  REQ-001/
    requirementreviewernode_v8.0.0.json
  REQ-007/
    requirementreviewernode_v8.0.0.json
```

---

## Cache Invalidation

Invalidation is **version-driven**. The cache version is extracted automatically from the PromptConfig template path using a semver regex:

```
hazard_h1/v1.0.0/template.jinja2  →  v1.0.0
synthesizer/v8.0.0/template.jinja2  →  v8.0.0
```

To bust the cache for a node, bump the version in its `PromptConfig` path (e.g., `v1.0.0` → `v1.1.0`). The new version produces a new key; old entries are simply never read again. No manual cache purge is required.

---

## Cache File Schema

Each disk file contains the serialised Pydantic model output and metadata needed to reconstruct the result and report savings:

```json
{
  "result": { ...model.model_dump()... },
  "meta": {
    "hazard_id": "GID-1234",
    "node": "h1evaluatornode",
    "prompt_version": "v1.0.0",
    "model": "gpt-4o-mini",
    "prompt_tokens": 1234,
    "completion_tokens": 456,
    "ts": "2026-05-25T10:30:00+00:00",
    "cache_tier_origin": 3
  }
}
```

`result` holds the raw `model_dump()` output captured **before** `_format_response` runs. On restore, the model is reconstructed via `ResponseModel.model_validate(cached["result"])` and then passed through `_format_response` as normal, so LangGraph state reducers receive properly-typed Pydantic instances.

---

## Node Coverage

| Node | Cached | Cache key entity | Notes |
|------|--------|-----------------|-------|
| `h1_evaluator` – `h7_evaluator` | Yes | `hazard.hazard_id` | Via `HazardEvaluatorNode._get_cache_entity_id` |
| `h6_evaluator` | Yes | `hazard.hazard_id` | Via `H6EvaluatorNode._get_cache_entity_id` |
| `design_summarizer` | Yes | `hazard.hazard_id` | Caches the full list of `HazardSummarizedDesignSpec` objects; batch token totals accumulated across all batches before the single cache write |
| `needs_summarizer` | Yes | `hazard.hazard_id` | Same pattern as `design_summarizer` with `HazardSummarizedUserNeed` |
| `final_assessment` | Yes | `hazard.hazard_id` | Stores `FinalAssessorProse` only (LLM-written comments). Verdicts are always re-computed deterministically from upstream `HazardFinding` objects in state — the LLM cannot re-grade them |
| `requirement_reviewer` | Yes | `requirement.req_id` | Keyed on `req_id`, not `hazard_id` — see cross-hazard deduplication below |
| `data_integration` | No | — | Not an LLM call |

---

## Cross-Hazard Requirement Deduplication

A key design decision in `RequirementReviewerNode` is that its cache key uses **`req_id`**, not `hazard_id`.

When a hazard analysis file is processed, the same requirement (e.g., `REQ-001`) often appears as a risk-control reference in multiple hazard rows. Without this design, the RTM subgraph would run once per hazard row that references `REQ-001` — identical inputs, identical cost, every time.

With `req_id` as the key, the RTM subgraph runs once for `REQ-001`. Every subsequent hazard row that references `REQ-001` hits the cache and returns the stored `RequirementReview` immediately.

The `prompt_version` used for this key is extracted from `PromptConfig.synthesizer` (the RTM synthesizer template path), so bumping the synthesizer version busts requirement review cache entries just as it does for hazard-level nodes.

Token savings for `requirement_reviewer` are measured by snapshotting the telemetry tracker's running totals before and after the RTM subgraph invocation:

```python
tokens_before_prompt = tracker._total_prompt_tokens
rtm_result = await self.rtm.graph.ainvoke(rtm_input)
prompt_tokens = tracker._total_prompt_tokens - tokens_before_prompt
```

---

## Integration into the Pipeline

`HazardCacheManager` is constructed once in `HazardReviewerRunnable.__init__` and passed to all 10 node factories in `build()`. The pipeline reads three environment variables (all have defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_HAZARD_CACHE` | `true` | Set to `false` to disable caching entirely |
| `HAZARD_CACHE_DIR` | `./cache/hazard` | Root directory for disk cache files |
| `REDIS_URL` | `None` | Redis connection URL, e.g. `redis://localhost:6379`. If unset, Tier 2 is disabled |

The pipeline reuses the `TokenUsageTracker` already wired into the `RateLimitOpenAIClient` so that cache events land in the same `token_usage.jsonl` file as normal LLM call records. A fresh tracker is never constructed here — doing so would clear the file.

---

## Code Architecture

### `autoqa/core/cache.py` — `HazardCacheManager`

Central class implementing the 3-tier logic. Key methods:

- `get(entity_id, node_name, prompt_version)` — checks Redis then disk; logs and emits telemetry on hit/miss; returns the raw payload dict or `None`
- `set(entity_id, node_name, prompt_version, result_dict, prompt_tokens, completion_tokens, model)` — writes to disk then Redis; swallows all exceptions
- `extract_prompt_version(template_path)` — static helper; parses `v{major}.{minor}.{patch}` from a template path string; falls back to `"default"`

### `autoqa/components/shared/nodes.py` — `StandardLLMNode`

Base class for all single-call LLM nodes. Cache support is added as a Template Method hook:

- `BaseLLMNode.__init__` accepts `cache_manager` and `prompt_version` (both optional, backward-compatible)
- `StandardLLMNode._get_cache_entity_id(state)` returns `None` by default; hazard subclasses override to return `hazard.hazard_id`
- `StandardLLMNode.__call__` inserts the cache check between state validation and `_build_payload`, and the cache write between parsing and `_format_response`

Nodes that do not override `_get_cache_entity_id` (or that have no `cache_manager`) skip the cache path entirely at zero cost.

### `autoqa/components/hazard_risk_reviewer/nodes.py`

Each node class participates in caching by overriding `_get_cache_entity_id` or implementing the cache check/write inline (for nodes with custom `__call__` implementations):

- `HazardEvaluatorNode` / `H6EvaluatorNode` — override `_get_cache_entity_id` to return `hazard.hazard_id`; cache logic runs via `StandardLLMNode.__call__`
- `HazardDesignSummarizerNode` / `HazardNeedsSummarizerNode` — custom `__call__` with inline check/write; token totals are accumulated across all batches before the single cache write
- `_FinalAssessorNode` — custom `__call__` (required because it must always produce a `HazardAssessment` even on LLM failure); caches `FinalAssessorProse` only; verdicts are re-computed from state regardless
- `RequirementReviewerNode` — no `StandardLLMNode` base; entirely custom `__call__` with inline check/write keyed on `req_id`

---

## Telemetry — `token_usage.jsonl` Events

Three new event types are appended to the existing JSONL file:

**Cache hit:**
```json
{
  "type": "cache_hit",
  "ts": "2026-05-25T10:30:00+00:00",
  "tier": 3,
  "node": "h1evaluatornode",
  "hazard_id": "GID-1234",
  "tokens_saved_prompt": 1234,
  "tokens_saved_completion": 456,
  "tokens_saved_total": 1690,
  "cost_saved_usd": 0.000460,
  "model": "gpt-4o-mini"
}
```

**Cache miss:**
```json
{
  "type": "cache_miss",
  "ts": "2026-05-25T10:30:00+00:00",
  "node": "h1evaluatornode",
  "hazard_id": "GID-1234"
}
```

**Session summary** (appended by `log_summary()`):
```json
{
  "type": "summary",
  "llm_calls": 10,
  "total_prompt_tokens": 12500,
  "total_completion_tokens": 4800,
  "total_cost_usd": 0.004755,
  "cache_hits_redis": 0,
  "cache_hits_disk": 9,
  "cache_misses": 10,
  "tokens_saved_by_cache": 15300,
  "cost_saved_by_cache_usd": 0.005085
}
```

Cache stats also appear in the `autoqa.log` summary line:

```
Token usage summary — calls: 10 | ... | cache hits: 9 (redis=0 disk=9) misses: 10 tokens_saved: 15,300 cost_saved: $0.0051
```

---

## Extending to Other Reviewers

The `StandardLLMNode` base class already has all hooks in place. To cache a node in another reviewer pipeline:

1. Inject a `HazardCacheManager` instance into the node constructor via `cache_manager=` and set `prompt_version=HazardCacheManager.extract_prompt_version(prompt_template)`.
2. Override `_get_cache_entity_id(self, state)` to return the entity identifier (e.g., `state["requirement"].req_id`).

No changes to `StandardLLMNode.__call__` are needed — the cache check and write run automatically when both `cache_manager` and a non-empty `prompt_version` are present.

For nodes with custom `__call__` implementations (like the summarizer nodes or `RequirementReviewerNode`), add the check/write blocks inline following the same pattern.
