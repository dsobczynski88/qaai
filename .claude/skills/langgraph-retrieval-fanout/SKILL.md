---
name: langgraph-retrieval-fanout
description: Use this skill when building a LangGraph workflow where multiple parallel nodes each evaluate the same input but retrieve context from different document sources or different filtered slices of the same source. Triggers include "build a langgraph workflow with retrieval at specific nodes", "fan out across documents", "RAG pipeline with multiple evaluators", "scaffold a multi-vector retrieval graph", "generalize the feedback-reviewer pattern", or any request to add a new evaluation dimension to a graph that already follows this shape. Generalizes the topology - state -> dispatch (Send fan-out) -> [node_i with retriever_i.retrieve(query, filter_i)] -> reduced lists.
version: 0.1.0
---

# LangGraph Retrieval Fan-out

Scaffold a LangGraph pipeline whose `START` fans an input list out to `N` parallel evaluator nodes, each retrieving from its own document slice, each producing a typed result that reducers merge into the final state.

## When to invoke

Invoke when the user's workflow has all four of these traits:

1. There are **≥2 evaluation/transformation dimensions** to apply to each input item.
2. Each dimension needs its **own retrieval context** (different document, different metadata filter, different query transform, or different `top_k`).
3. The dimensions are **independent** — none of them needs another's output.
4. The output is a **list** of typed results per dimension that should be returned together.

Concrete trigger phrasings: "build a langgraph workflow with retrieval at specific nodes", "fan out across documents", "RAG pipeline with multiple evaluators", "score each item along several dimensions, each with its own context", "scaffold a multi-vector retrieval graph".

## When to skip

- **One dimension only** → just write a single async node, no graph needed.
- **Same retrieval context across nodes** → use one node and call the LLM once with all the prompts, or one retrieval + N LLM calls in sequence — the graph doesn't earn its weight.
- **Nodes have data dependencies** (node B needs node A's output) → use a sequential graph with `add_edge(A, B)`, not Send fan-out.
- **Iterative / agentic** (the workflow loops, escalates, or self-corrects) → this is a different pattern (ReAct / supervisor-worker); this skill won't help.

## The pattern in one diagram

```
                                   ┌──── node_1  (retriever.retrieve(q1, filter_1))  ──┐
                                   │                                                   │
START ── dispatch (Send per item) ─┼──── node_2  (retriever.retrieve(q2, filter_2))  ──┼── END
                                   │                                                   │
                                   └──── node_N  (retriever.retrieve(qN, filter_N))  ──┘

State:                              Each node returns:
  inputs:        List[Input]          {state_key_i: [one_typed_result]}
  results_1:     Annotated[List[T1], operator.add]   <- reducer merges parallel returns
  results_2:     Annotated[List[T2], operator.add]
  ...
  results_N:     Annotated[List[TN], operator.add]
```

Reference instantiation: `multi-vec-retrieval` (FeedbackReviewer) — three nodes (Critique, Improvement, Compliance), each retrieving from `SectionExpandingRetriever` with different `(doc_name, section_name)` filters; results merged into `critiques`, `improvements`, `compliance` lists. See `src/multivec/components/shared/{core,nodes,pipeline}.py` and `src/multivec/components/retrieval/retriever.py` if the user's repo is that one.

## Phase 1 — Define the state schema

Decide:

- **Input list type** — a Pydantic `BaseModel` for each input item (e.g., `Comment`, `Document`, `Question`).
- **One output Pydantic model per node**. Each output model **must** carry:
  - The input identifier (`comment_id`, `doc_id`, ...) so results can be joined back.
  - A `retrieved_chunk_ids: List[str]` (or analogous) field — the audit trail of which chunks contributed. Without it, you can't debug why a node produced a given answer.
- **A `TypedDict` state** with one `Annotated[List[ResultI], operator.add]` field per node, plus the input list:

  ```python
  class MyState(TypedDict):
      inputs: List[InputModel]
      results_1: Annotated[List[Result1], operator.add]
      results_2: Annotated[List[Result2], operator.add]
      # ... one per node
  ```

- **An `EvalTask` TypedDict** as the per-Send payload — usually just `{"item": one_input}`. Keep it minimal; the node looks up everything else through closures (retriever, jinja env).

Copy `references/state.py.template` and rename the placeholders.

**Common mistake:** forgetting `Annotated[..., operator.add]`. Without the reducer, parallel Sends overwrite each other and you'll see only the last node's result.

## Phase 2 — Map nodes to retrieval surfaces

For each node, write down on one line:

| Node | Source(s) | Filter | Query transform | top_k |
|------|-----------|--------|-----------------|-------|
| node_1 | doc_A.md | section matching `item.section_label` | `f"{item.section} :: {item.body}"` | 3 |
| node_2 | doc_A.md | (same as 1) | `item.body` (raw) | 5 |
| node_3 | guidance.md | none | `f"{item.body} {item.response}"` | 3 |

This table is the contract. If two nodes have the same source + filter + transform + `top_k`, collapse them — the graph buys you nothing.

See `references/pattern-notes.md` for the decision tree on shared-vs-per-node retrievers and the four most common mistakes.

## Phase 3 — Build the retriever

Default to **one shared retriever instance, with per-call filters** (the FeedbackReviewer choice). Reasons:

- Single Chroma collection → less disk, less startup time.
- Filter at query time via Chroma metadata (`{"parent_doc_name": {"$eq": ...}}`) — cheap.
- A node that wants a different source just passes `doc_name="other.md"`.

Use a separate retriever only when the sources have **incompatible chunk schemas** (e.g., one is markdown sections, the other is JSON records).

Embeddings: namespace the Chroma persistence directory by an `embedding_slug(model_name)` so swapping models doesn't mix vector spaces. Support both OpenAI and local sentence-transformers via prefix dispatch (`hf:` or `sentence-transformers/` → `HuggingFaceEmbeddings`; else `OpenAIEmbeddings`).

Copy `references/retriever.py.template`. Implement `retrieve_with_ids(query, **filters) -> (context_string, [chunk_ids])` and a thin `retrieve(...)` wrapper that discards the ids.

**Critical:** the retriever returns *both* a formatted string for the prompt *and* the ordered `chunk_ids` so the node can stamp them onto the result for traceability. Don't lose the ids.

## Phase 4 — Build the node class(es)

Use the Template Method pattern (mirrors `nodes.py` in the reference repo):

```
BaseLLMNode (ABC)         <- response parsing (JSON-from-markdown), client + model config
    StandardLLMNode (ABC) <- the async __call__: build messages, call LLM, parse, inject meta, dispatch
        _EvalNodeBase     <- holds retriever + jinja_env, defines _format_response = {state_key: [parsed]}
            ConcreteNode  <- sets prompt_template + state_key, implements _build_prompt_and_meta
```

Each concrete node is ~10 lines: it retrieves, renders the Jinja template, returns `(prompt, {"retrieved_chunk_ids": chunk_ids})`. The base class handles everything else.

**Why this layering:** parsing logic, message construction, and LLM calling never change between nodes. Only the prompt-build and the result key do. Extracting the invariant into the base classes means adding a new evaluation dimension is a 10-line subclass + a Jinja file.

Copy `references/nodes.py.template`.

**Common mistake:** returning `parsed.model_dump()` or `parsed` directly from a node. The reducer expects `{state_key: [parsed]}` — a dict with a list whose only element is the result. The list lets `operator.add` merge across parallel Sends.

## Phase 5 — Wire the graph

```python
def dispatch(state: MyState) -> List[Send]:
    sends: List[Send] = []
    for item in state.get("inputs", []):
        payload: EvalTask = {"item": item}
        sends.append(Send("node_1", payload))
        sends.append(Send("node_2", payload))
        sends.append(Send("node_3", payload))
    return sends


builder = StateGraph(MyState)
builder.add_node("node_1", node_1_instance)
builder.add_node("node_2", node_2_instance)
builder.add_node("node_3", node_3_instance)

builder.add_conditional_edges(START, dispatch, ["node_1", "node_2", "node_3"])
builder.add_edge("node_1", END)
builder.add_edge("node_2", END)
builder.add_edge("node_3", END)

graph = builder.compile()
```

Copy `references/pipeline.py.template` for the full `Runnable` class form (constructor, retriever wiring, jinja env, model_kwargs).

## Phase 6 — Verify

1. **Smoke test with one input:**
   ```python
   result = await graph.ainvoke({
       "inputs": [one_item],
       "results_1": [], "results_2": [], "results_3": [],
   })
   ```
2. **Each list populated:** `assert len(result["results_1"]) == 1` for every node — confirms the dispatch wired all `N` nodes.
3. **Trace per result:** every result item should have a non-empty `retrieved_chunk_ids` (or `[no relevant context retrieved]` flagged). If a node's `chunk_ids` are always empty but other nodes' aren't, the filter is too tight.
4. **Multiple inputs:** with 2 items and 3 nodes, expect `len(results_*) == 2` for each, in any order. If you see 6 items in one list and 0 in another, your `_format_response` is returning the wrong key.
5. **Model swap:** if supporting multiple embedding backends, run once with each (or `--embedding-model sentence-transformers/all-MiniLM-L6-v2`) and confirm Chroma directories are namespaced separately under `docs/chunked/chroma/<slug>/`.

## Templates

All under `references/` next to this file:

- **`state.py.template`** — input model + N result models + state TypedDict + EvalTask
- **`retriever.py.template`** — `SectionExpandingRetriever` with metadata filtering and parent-section expansion + `build_retriever` factory
- **`nodes.py.template`** — `BaseLLMNode` → `StandardLLMNode` → `_EvalNodeBase` → one example concrete node + `dispatch` function
- **`pipeline.py.template`** — `Runnable` class wiring `StateGraph`, conditional edges, compilation
- **`pattern-notes.md`** — decision tree (one-vs-N retrievers, OpenAI vs HF embeddings, when to use this skill at all) and the four most common mistakes

Each `.py.template` is valid Python with `# TODO(rename)` markers — `python -m py_compile` after rename to `.py` and confirm.
