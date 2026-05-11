# Pattern notes — LangGraph retrieval fan-out

Decision tree, design tradeoffs, and the four most common mistakes. Read this before you start writing the templates.

## Topology decision tree

```
Q: How many evaluation dimensions per input?
   1            -> single async node. Don't use this skill.
   ≥2           -> continue.

Q: Do the dimensions need different retrieval contexts?
   No           -> one node, one retrieval, N LLM calls in sequence (or one merged prompt).
   Yes          -> continue.

Q: Are the dimensions independent (no node B reads node A's output)?
   No           -> sequential graph: START -> A -> B -> ... -> END.
   Yes          -> continue. THIS is the fan-out pattern.

Q: Is the workflow iterative (escalation, self-correction, agentic loop)?
   Yes          -> different pattern (supervisor, ReAct). This skill won't help.
   No           -> use this skill. Send fan-out from START to all N nodes.
```

## Shared retriever vs. one retriever per source

**Default: one shared `SectionExpandingRetriever` instance, with per-call filters.**

Use one retriever when:

- All sources can be chunked with the same chunker (e.g., all markdown with `##` headings).
- Per-node retrieval differs only in metadata filter, query transform, or `top_k`.
- You want a single Chroma collection (smaller disk, faster startup).

Use **multiple retrievers** when:

- Sources have **incompatible chunk schemas** (markdown sections vs. JSON records vs. tabular rows).
- One source needs a fundamentally different similarity model (e.g., code embeddings vs. text embeddings).
- One source is so large you want it on a separate vector backend (e.g., one Chroma collection per source).

The reference workflow (multi-vec-retrieval) uses **one retriever**, three filters: `(doc=SOP, section=resolved)`, `(doc=SOP, section=resolved)` again with a different query transform, and `(doc=fda.md, section=None)`.

## OpenAI vs. local sentence-transformers embeddings

| Use OpenAI when                              | Use sentence-transformers when                  |
|----------------------------------------------|-------------------------------------------------|
| You're already calling OpenAI for the LLM    | You don't have an OpenAI key (offline, gov, dev)|
| Latency dominated by LLM, embedding is small | High-volume embedding, want fixed CPU cost      |
| You want SOTA retrieval quality              | Workflow tolerates ~5-15% retrieval-quality drop|

**Always namespace the persistence directory by an embedding-model slug** (`docs/chunked/chroma/<slug>/`). Mixing vector spaces inside one Chroma collection silently gives nonsense results — distances aren't comparable across embedding models.

## Adding a new evaluator dimension to an existing graph

1. Add a new result Pydantic model with `item_id` + `retrieved_chunk_ids` + your dimension fields.
2. Add a new `Annotated[List[NewResult], operator.add]` field to the state TypedDict.
3. Add a new concrete `_EvalNodeBase` subclass: set `prompt_template`, `state_key`, implement `_build_prompt_and_meta`.
4. Add a new Jinja prompt file under `prompts_dir`.
5. In the runnable's `__init__`: instantiate the node, `add_node(...)`, append the name to the conditional edges list, `add_edge(name, END)`.
6. In `dispatch`: append a `Send("new_node", payload)` per input item.
7. In any caller building the initial state: add the empty list `"new_results": []`.

Six places. If you skip any one, the symptom is a missing list, an `AttributeError`, or a silently dropped result. Grep for the existing node's name before adding to find every site.

## The four most common mistakes

1. **Forgetting `Annotated[List[T], operator.add]` on the state field.** Without the reducer, parallel Sends overwrite each other in the merged state. Symptom: only one node's result is present in the output. Fix: always `Annotated[List[T], operator.add]` for any field a parallel node writes.

2. **Returning the wrong shape from `_format_response`.** The reducer expects `{state_key: [one_item]}` — a dict whose value is a single-element list. If you return `parsed` directly, or `{state_key: parsed}` (without the list), `operator.add` will either fail or produce garbage. Fix: always `return {self.state_key: [parsed]}`.

3. **Dropping `retrieved_chunk_ids`.** The retriever returns `(context_string, [chunk_ids])`. If the node only uses the string, you lose the per-result audit trail and can't debug why a node produced a given answer. Fix: thread the ids through `_build_prompt_and_meta` as `{"retrieved_chunk_ids": chunk_ids}` so `__call__` can stamp them onto the parsed result.

4. **Not namespacing Chroma per embedding model.** If you persist to a single `chroma/` directory and swap embedding models, the new model writes into a vector space populated by the old model — distances are meaningless. Symptom: retrieval returns nonsense after a model swap. Fix: persistence path includes `embedding_slug(model_name)`, and on swap either reuse the namespaced dir or rebuild.

## When this skill is the wrong tool

- You're doing a **single retrieval feeding multiple prompts** — no graph needed; one async function with N LLM calls suffices.
- You want **agentic / iterative** behavior — different pattern (supervisor, ReAct, plan-and-execute).
- You're doing **map-reduce style document summarization** — LangChain's `MapReduceDocumentsChain` or a simpler `asyncio.gather` is shorter.
- The dimensions have **strong data dependencies** — sequential graph, not fan-out.
