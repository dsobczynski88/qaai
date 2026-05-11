---
name: rag-document-intelligence
description: Layer 0 of production RAG architecture. Use when building a document corpus where naive chunking destroys structure — tables, multi-section papers, SOPs, clinical guidelines. Triggers include "chunks mix section types", "retrieval loses table context", "importance of abstract vs methodology differs", "need section-aware splitting for [domain] documents", or any corpus where a document has distinct sections with different retrieval value. Companion skills: rag-query-intelligence (Layer 1), rag-multi-stage-retrieval (Layer 2), rag-context-assembly (Layer 3), rag-generation-validation (Layer 4).
---

# Layer 0: Document Intelligence

The root cause of most poor RAG retrieval is that documents have **structure** but naive chunkers treat them as flat text. A medical paper's abstract, methodology, and results each have different retrieval value. An SOP's scope section and its step-by-step procedures are retrieved for fundamentally different queries. Destroying this structure before indexing means retrieval can never recover it.

This skill adds a document intelligence layer that runs **before** any embedding or vector store operation.

## The pattern in one diagram

```
raw files (docx / md / pdf)
    │
    ▼  _parse_to_markdown()           ← convert binary formats first
parsed/*.md
    │
    ▼  _detect_sections()             ← structure-aware split (not fixed-size)
List[Section]  (title, type, content)
    │
    ▼  _score_importance()            ← section_type weights + entity density
List[ScoredSection]  (+ importance_score)
    │
    ▼  _extract_entities()            ← domain NER for metadata enrichment
List[DocumentSection]  (+ entities[])
    │
    ▼  semantic_chunk_sections()      ← break large sections into embedding-sized chunks
List[Document]  (chunks with full parent metadata)
    │
    ▼  persist() + build_or_load_vectorstore()
Chroma / JSONL
```

## Phase 1: Choose your splitting strategy by document type

Do not use one splitting strategy for all documents. Match the splitter to the structure:

| Document type | Splitter | Metadata captured |
|---|---|---|
| Markdown with `##` headings | `MarkdownHeaderTextSplitter` on `##` | `section_name`, `doc_name` |
| Design/labeling docs with ID markers | Custom regex on `Global ID: GID-N` | `global_id`, `project_id`, `title` |
| Scientific papers with structured headers | Regex on ABSTRACT / METHODS / RESULTS | `section_type`, `importance_score` |
| PDFs / Word docs | Convert to markdown first, then heading splitter | same as markdown |

The existing `chunker.py` in this repo demonstrates both cases:
- `split_by_heading(md_text, doc_name)` — generic `##` split, returns parent sections
- `split_by_design_marker(md_text, doc_name, pattern)` — regex split capturing `GID` + `project_id`

## Phase 2: Assign section importance scores

After splitting, score each section **before** embedding. Store the score in chunk metadata so retrieval and reranking can use it downstream.

```python
SECTION_TYPE_WEIGHTS = {
    "abstract":     1.0,
    "results":      0.9,
    "conclusion":   0.8,
    "methodology":  0.6,
    "introduction": 0.5,
    "references":   0.2,
}

def score_section(section_type: str, content: str, entities: list[str]) -> float:
    base = SECTION_TYPE_WEIGHTS.get(section_type, 0.4)
    entity_density = len(entities) / max(len(content.split()), 1)
    return min(base + min(entity_density * 0.3, 0.3), 1.0)
```

Store `importance_score` in the chunk's `metadata` dict. Downstream rerankers (Layer 2) combine this with vector similarity scores.

## Phase 3: Domain entity extraction for metadata enrichment

Extract domain entities and store them in chunk metadata. This enables:
- Metadata filters at retrieval time ("only chunks mentioning disease X")
- Importance boosting based on entity density
- Cross-chunk entity graphs (future)

```python
# For scientific/medical domains: use spaCy with a domain model
# en_core_sci_md for biomedical; en_core_web_sm for general
ENTITY_LABELS_TO_KEEP = {"DISEASE", "CHEMICAL", "GENE", "ANATOMY"}  # adjust per domain

def extract_entities(doc) -> list[str]:
    return list({ent.text for ent in doc.ents if ent.label_ in ENTITY_LABELS_TO_KEEP})
```

For non-NLP domains: use regex or keyword lists instead of spaCy — don't add a heavy dependency if your entities are enumerable.

## Phase 4: Propagate parent metadata to chunks

When `semantic_chunk_sections()` splits a section into embedding-sized chunks, every child chunk **must inherit** the parent's structured metadata. Without this, a retrieved chunk loses its provenance.

```python
# Pattern from chunker.py in this repo
_PROPAGATED_SECTION_META = ("global_id", "project_id")

for chunk in raw_chunks:
    for key in _PROPAGATED_SECTION_META:
        if key in section.metadata:
            chunk.metadata[f"parent_{key}"] = section.metadata[key]
    chunk.metadata["parent_doc_name"] = section.metadata["doc_name"]
    chunk.metadata["parent_section_name"] = section.metadata["section_name"]
    chunk.metadata["importance_score"] = section.metadata.get("importance_score", 0.5)
```

## Phase 5: Idempotent build with persist/reload

Semantic chunking is expensive (one embedding call per breakpoint). Persist the result and reload on subsequent runs.

```python
# Pattern: check for persisted JSONL, skip re-chunking if present
if not force and (chunked_dir / "chunks.jsonl").exists():
    chunks, sections = load_persisted(chunked_dir)
else:
    sections = split_by_heading(md_text, doc_name)
    chunks = semantic_chunk_sections(sections, embeddings)
    persist(chunks, sections, chunked_dir)
```

**Warning:** The persisted JSONL is tied to the embedding model that set the chunk boundaries. Swapping `--embedding-model` changes the vector space but does **not** re-chunk. Delete the JSONL pair and rebuild if you want new chunk boundaries.

## Verification

After running the build pipeline, verify:
1. `sections.jsonl` — each section has `section_name`, `doc_name`, and `importance_score`
2. `chunks.jsonl` — each chunk has `parent_doc_name`, `parent_section_name`, `importance_score`
3. Spot-check: load a chunk from `results` section of a paper and confirm its `importance_score ≈ 0.9`
4. Spot-check: confirm no chunk's text spans two different section types

## Common mistakes

**Mistake 1: Fixed-size chunking destroys tables.**
`RecursiveCharacterTextSplitter(chunk_size=500)` will split a 12-row table in half. Use heading-based splits first, then semantic chunking within sections.

**Mistake 2: Chunking without section type in metadata.**
If chunks don't know they're from an "abstract" vs "results", the reranker in Layer 2 can't use importance scores. Add `section_type` to metadata at split time.

**Mistake 3: One splitter strategy for all doc types.**
SOPs split well on `##`. Design specs split on `Global ID:` markers. Clinical papers split on structured headers. Forcing one strategy corrupts all the others.

**Mistake 4: Rebuilding the vector store without rebuilding chunks.**
The Chroma `--force` flag rebuilds the vector store from existing JSONL. If you want new chunk boundaries, you must delete the JSONL pair first.
