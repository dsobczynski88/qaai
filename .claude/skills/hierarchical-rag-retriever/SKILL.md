---
name: hierarchical-rag-retriever
description: Use this skill when building or generalizing a two-level hierarchical RAG retrieval substrate — a corpus builder that splits documents into parent sections and semantic child chunks, plus a SectionExpandingRetriever that optionally fuses dense (Chroma) and sparse (BM25) search results via Reciprocal Rank Fusion. Triggers include "build a retriever for a new document corpus", "add BM25 hybrid search to Chroma retrieval", "generalize copy-paste retrievers into one factory", "activate the metadata filter on retrieve_with_ids", "chunks not inheriting global_id or project_id", or any request to extend multi-vec-retrieval with a new corpus type. The companion skill langgraph-retrieval-fanout covers the GRAPH TOPOLOGY layer; this skill covers the RETRIEVAL SUBSTRATE layer they call into.
version: 0.1.0
---

# Hierarchical RAG Retriever

Scaffold a corpus builder and retriever class that implement the two-level hierarchy pattern: parent sections (deterministic splitter) → child semantic chunks (SemanticChunker) → Chroma vectorstore with optional BM25 hybrid search and a filter-fallback chain.

## Relationship to `langgraph-retrieval-fanout`

These two skills compose:

```
langgraph-retrieval-fanout   ← graph topology (state, dispatch, nodes, pipeline)
        |
        └── each node calls:
hierarchical-rag-retriever   ← retrieval substrate (corpus, chunker, retriever)
```

Use `langgraph-retrieval-fanout` to build the graph. Use this skill to implement the retriever each node calls. The concrete reference repo (`multi-vec-retrieval`) is a working instantiation of both skills: `SectionExpandingRetriever` is built by `build_retriever(corpus_cfg, ...)` and called from `ProcessTopicNode` and the eval nodes in `nodes.py`.

## When to invoke

Invoke when the user needs any of:

1. A **new document corpus** — raw files (`.docx`, `.md`, `.pdf`) that need to become a Chroma-backed retriever without copy-pasting `design_corpus.py` or `design_retriever.py`.
2. **Hybrid search** — BM25 + dense Chroma fused with Reciprocal Rank Fusion, added to an existing retriever.
3. **Filter fallback** — `retrieve_with_ids(query, doc_name, section_name)` should try (doc+section) → (doc only) → unfiltered, but the live `retriever.py:69` always passes `None` — this skill fixes that.
4. **Metadata propagation** — chunks not inheriting extra section fields (`global_id`, `project_id`) because `_PROPAGATED_SECTION_META` was not updated after adding fields to the splitter.
5. **Corpus idempotency** — re-chunking accidentally when persisted JSONL exists, or the reverse: stale JSONL surviving a schema change.

## When to skip

- You only need the **graph topology** and already have a working retriever — use `langgraph-retrieval-fanout` alone.
- You are doing **one-shot retrieval** with no parent-section expansion — a plain `Chroma.similarity_search` is sufficient.
- Your corpus is already chunked correctly and stored — no need to rebuild the corpus builder.
- You need **full-text search only** with no vector component — use a dedicated search library instead.

## The pattern in one diagram

```
raw files (docx / md / pdf)
    │
    ▼  _convert_all_raw()            (corpus.py.template)
parsed/*.md
    │
    ▼  corpus_cfg.splitter_fn(text, doc_name)
List[Section Document]
    {doc_name, section_name, section_id, ...extra_meta_keys}
    │
    ▼  semantic_chunk_sections(sections, embeddings, extra_meta_keys)
List[Chunk Document]
    {parent_doc_name, parent_section_name, parent_section_id, chunk_id,
     parent_<extra_meta_key>, ...}          <- propagated via extra_meta_keys
    │
    ├── persist(chunks, sections, chunked_dir)   -> chunks.jsonl, sections.jsonl
    │
    └── build_or_load_vectorstore(chunks, chroma_dir, embeddings, collection_name)
            │
            ▼
        SectionExpandingRetriever
            ._search_dense()   }
            ._search_sparse()  } -> _fuse_rrf() -> List[Document]
            ._format()         -> context string + parent section full text
            .retrieve_with_ids(query, doc_name, section_name)
                filter fallback: (doc+section) -> (doc only) -> unfiltered
```

## Phase 1 — Define the CorpusConfig

`CorpusConfig` is a dataclass that parameterizes everything corpus-specific. One config replaces one copy-pasted `*_corpus.py` + `*_retriever.py` pair.

Fields:

| Field | Type | Example (SOP corpus) | Example (design corpus) |
|---|---|---|---|
| `name` | `str` | `"sop"` | `"design"` |
| `collection_name` | `str` | `"chunks"` | `"design_chunks"` |
| `chroma_subdir` | `str` | `""` | `"design"` |
| `raw_dir` | `Path` | `docs/raw` | `docs/raw/design` |
| `parsed_dir` | `Path` | `docs/parsed` | `docs/parsed/design` |
| `chunked_dir` | `Path` | `docs/chunked` | `docs/chunked/design` |
| `splitter_fn` | `Callable[[str, str], List[Document]]` | `split_by_heading` | `split_by_design_marker` |
| `extra_meta_keys` | `Tuple[str, ...]` | `()` | `("global_id", "project_id")` |
| `raw_extension` | `str` | `".md"` | `".docx"` |
| `top_k` | `int` | `3` | `5` |

`extra_meta_keys` replaces the module-level `_PROPAGATED_SECTION_META` tuple in the live `chunker.py`. It tells `semantic_chunk_sections` which additional section fields to propagate onto chunks as `parent_<key>`. This is now per-corpus rather than a global constant.

Copy `references/corpus.py.template` and rename the `# TODO(rename)` markers.

**Decision point:** if your corpus has `.docx` files, set `raw_extension=".docx"` and wire `_docx_to_md` in `build_corpus()`. If `.pdf`, add a `_pdf_to_md` converter. If raw files are already `.md`, set `raw_extension=".md"` and `_convert_all_raw` is a no-op.

**Common mistake:** pointing `raw_dir` and `parsed_dir` at the same path. `_convert_all_raw` writes converted files to `parsed_dir`; if they're the same directory, the converter re-processes its own output on the next run.

## Phase 2 — Build the corpus (splitter + chunk metadata propagation)

`build_corpus(corpus_cfg, force, embedding_model)` replaces both `build_all()` (SOP path) and `build_design_corpus()` (design path). It:

1. Checks `load_persisted(corpus_cfg.chunked_dir)` — returns cached chunks+sections if present and `force=False`.
2. Calls `_convert_all_raw(corpus_cfg)` to produce `.md` files.
3. Iterates `.md` files and calls `corpus_cfg.splitter_fn(text, doc_name)`.
4. Calls `semantic_chunk_sections(sections, embeddings, extra_meta_keys=corpus_cfg.extra_meta_keys)`.
5. Calls `persist(chunks, sections, corpus_cfg.chunked_dir)`.

`semantic_chunk_sections` accepts `extra_meta_keys` as a parameter (overriding the module-level tuple) so propagation is controlled by the corpus config, not a global constant. Keys absent from a section are skipped silently — the SOP path (`extra_meta_keys=()`) is unaffected.

**When to set `force=True`:** whenever `extra_meta_keys` changes, the splitter regex changes, or source files are replaced. Stale JSONL serves outdated chunks silently.

See `references/corpus.py.template`.

## Phase 3 — Build the retriever (dense vs hybrid)

`build_retriever(corpus_cfg, top_k, embedding_model, use_hybrid)` is the factory. It:

1. Calls `load_or_build(corpus_cfg, embedding_model=model_name)` to get chunks + sections.
2. Builds `chroma_dir = corpus_cfg.chunked_dir / "chroma" / embedding_slug(model_name) / corpus_cfg.chroma_subdir`.
3. Calls `build_or_load_vectorstore(chunks, chroma_dir, embeddings, collection_name=corpus_cfg.collection_name)`.
4. Returns `SectionExpandingRetriever(vs, sections, chunks=chunks, top_k=top_k, use_hybrid=use_hybrid)`.

The Chroma path is namespaced by **both** `embedding_slug` (vector spaces never mix across model swaps) **and** `corpus_cfg.chroma_subdir` (design chunks never contaminate SOP chunks within the same model's directory).

**Dense-only (default):** `use_hybrid=False`. `_search()` calls `_search_dense()` only. This is the current live behavior.

**Hybrid mode:** `use_hybrid=True`. `_search()` calls `_search_dense()` and `_search_sparse()`, then `_fuse_rrf()`. Output is still `List[Document]` — `_format()` is unchanged. Requires `rank_bm25` (`pip install rank-bm25`). The BM25 index is built lazily on the first hybrid call from chunk `page_content`; not persisted to disk.

**RRF formula:** score(d) = sum over each ranked list of `1 / (k + rank(d))`, where k=60. Documents in both lists get contributions from both; truncated to `top_k`.

**Filter fallback chain (the live code bug fix):**

The live `retrieve_with_ids` always passes `None` to `_search`. The fix:
```python
if doc_name and section_name:
    filter_ = {"$and": [{"parent_doc_name": {"$eq": doc_name}},
                         {"parent_section_name": {"$eq": section_name}}]}
elif doc_name:
    filter_ = {"parent_doc_name": {"$eq": doc_name}}

hits = _search(query, filter_)
if not hits and doc_name and section_name:
    hits = _search(query, {"parent_doc_name": {"$eq": doc_name}})
if not hits:
    hits = _search(query, None)
```

**Exception logging:** the live code swallows exceptions with `except Exception: return []`. The template adds `logger.warning(...)` before returning the empty list.

See `references/retriever.py.template`.

## Phase 4 — Wire into a LangGraph node

In a node following the `langgraph-retrieval-fanout` pattern:

```python
query = f"{item.topic_name} :: {summary[:500]}"
context, chunk_ids = self.retriever.retrieve_with_ids(
    query,
    doc_name="design_doc.md",   # or None for unfiltered
    section_name=None,
)
```

To construct the retriever in a pipeline `__init__`:

```python
from your_pkg.corpus import CorpusConfig
from your_pkg.retriever import build_retriever
from your_pkg.chunker import split_by_design_marker

design_cfg = CorpusConfig(
    name="design",
    collection_name="design_chunks",
    chroma_subdir="design",
    raw_dir=settings.design_raw_dir,
    parsed_dir=settings.design_parsed_dir,
    chunked_dir=settings.design_chunked_dir,
    splitter_fn=split_by_design_marker,
    extra_meta_keys=("global_id", "project_id"),
    raw_extension=".docx",
    top_k=settings.design_top_k,
)
retriever = build_retriever(
    corpus_cfg=design_cfg,
    top_k=top_k or design_cfg.top_k,
    embedding_model=embedding_model or settings.embedding_model,
    use_hybrid=False,   # flip to True to enable BM25 fusion
)
```

This replaces `build_design_retriever(top_k, embedding_model)` with zero behavioral change. To add a SOP corpus alongside it, define a `sop_cfg` using `split_by_heading` and `extra_meta_keys=()`, then call `build_retriever(sop_cfg, ...)` — no new file needed.

## Phase 5 — Verify

1. **Section metadata smoke test:** call `corpus_cfg.splitter_fn(sample_text, "test.md")` and assert each section has `doc_name`, `section_name`, `section_id`, and all keys in `extra_meta_keys`.

2. **Chunk propagation test:** call `semantic_chunk_sections(sections, embeddings, extra_meta_keys=cfg.extra_meta_keys)` and assert each chunk has `parent_{key}` for all keys in `extra_meta_keys` that were present on its parent section.

3. **Filter fallback test:** build a retriever with a small corpus of two docs. Call `retrieve_with_ids(query, doc_name="doc_a.md", section_name="NONEXISTENT")` — should fall back to doc-only filter, then unfiltered, and return non-empty results. Assert `chunk_ids` is non-empty.

4. **Hybrid vs dense output:** with `use_hybrid=False` and `use_hybrid=True`, call `retrieve_with_ids(query)` on the same query. Assert both return non-empty strings and `chunk_ids` lists.

5. **Corpus idempotency:** call `load_or_build(cfg)` twice. Assert the second call returns immediately — mock the embeddings and assert the mock is not called on the second invocation.

6. **Chroma namespace isolation:** build two retrievers with two different `CorpusConfig` objects. Assert `vs1.persist_directory != vs2.persist_directory`.

See `references/test_retrieval.py.template`.

## How to add a new corpus to an existing workflow

Six steps. Grep for the existing corpus name (e.g., `"design"`) before starting to find every site.

1. **Define a `CorpusConfig`** — set all fields. If the corpus uses a new marker pattern, define a `split_by_<corpus>_marker` function in `chunker.py` following the `split_by_design_marker` pattern.

2. **Add directory settings** — add `<corpus>_raw_dir`, `<corpus>_parsed_dir`, `<corpus>_chunked_dir` to `Settings`. Add `<corpus>_top_k` if the retrieval depth should differ.

3. **Call `build_retriever(corpus_cfg, ...)`** — in the pipeline `__init__` or wherever retrievers are constructed. No new `*_retriever.py` file needed.

4. **Drop raw files** — place `.docx`/`.md`/`.pdf` under `<corpus>_raw_dir`. On first run, `build_corpus` converts them and produces `chunks.jsonl` + `sections.jsonl`.

5. **Wire into a LangGraph node** — pass the retriever to the node. The node calls `retriever.retrieve_with_ids(query, doc_name=..., section_name=...)`.

6. **Verify** — run the five checks from Phase 5 with the new `CorpusConfig`. Pay special attention to step 1 (section metadata) to confirm `extra_meta_keys` are present on sections, and step 2 (propagation) to confirm `parent_<key>` is on chunks.

## Common mistakes

**1. Forgetting to update `extra_meta_keys` when the splitter adds new metadata fields.**

If `split_by_design_marker` adds `global_id` and `project_id` to sections but `extra_meta_keys=()` in the `CorpusConfig`, chunks will not carry `parent_global_id` / `parent_project_id`. Downstream code silently gets `None`. Fix: set the correct `extra_meta_keys`, delete the stale JSONL, and rebuild.

**2. The live code filter bug — `retrieve_with_ids` always passes `None` to `_search`.**

In `retriever.py:69`, `retrieve_with_ids` calls `self._search(query, None)` regardless of `doc_name` / `section_name`. All retrieval is unfiltered. Symptom: nodes that should retrieve from one doc return chunks from all docs. Fix: use the filter fallback chain from `references/retriever.py.template`.

**3. Re-chunking without deleting persisted JSONL after changing `extra_meta_keys`.**

`load_or_build` returns cached JSONL if it exists, regardless of whether `extra_meta_keys` changed. Old chunks (without `parent_global_id`) are served from cache silently. Fix: delete `chunks.jsonl` and `sections.jsonl` under `corpus_cfg.chunked_dir` before rebuilding, or call `build_corpus(cfg, force=True)`.

**4. Not namespacing Chroma per corpus (`chroma_subdir` left as `""` for two corpora).**

If two corpora both use `chroma_subdir=""`, their Chroma directories resolve to the same path. The first corpus's chunks contaminate the second's vectorstore. Symptom: design-corpus queries return SOP chunks. Fix: give each corpus a distinct `chroma_subdir` (e.g., `"design"`, `"sop"`, `"guidance"`).

## Templates

All under `references/` next to this file:

- **`corpus.py.template`** — `CorpusConfig` dataclass + `build_corpus()` + `load_or_build()` + `_convert_all_raw()` + `_docx_to_md()` + `semantic_chunk_sections()` with per-call `extra_meta_keys`.
- **`retriever.py.template`** — `SectionExpandingRetriever` with hybrid search (`_search_dense`, `_search_sparse`, `_fuse_rrf`), filter fallback chain, logged exceptions, and `build_retriever(corpus_cfg, ...)` factory. Also includes `_eval_filter` for post-hoc BM25 candidate filtering.
- **`test_retrieval.py.template`** — pytest test suite covering: section splitting metadata, chunk metadata propagation, filter fallback behavior, RRF fusion, corpus idempotency, and Chroma namespace isolation.

Each `.py.template` is valid Python with `# TODO(rename)` markers. After copying to your project, rename the markers, then run `python -m py_compile <file>.py` to confirm syntax.