---
name: rag-multi-stage-retrieval
description: Layer 2 of production RAG architecture. Use when single-stage vector search returns high-similarity but wrong-intent results, or when document quality/recency should influence ranking, or when multiple sections from the same document should be fused into a document-level score. Triggers include "retrieval returns semantically close but irrelevant chunks", "need cross-encoder reranking", "document-level scoring rather than chunk-level", "metadata filters not reducing candidate noise enough", or "want to boost results from higher-quality sources". Companion skills: rag-query-intelligence (Layer 1, produces QueryAnalysis input), rag-context-assembly (Layer 3, consumes retrieval output). Relates to: hierarchical-rag-retriever (the retrieval substrate this layer sits on top of).
---

# Layer 2: Multi-Stage Retrieval

Vector search alone fails in production because cosine similarity measures **embedding proximity**, not **answer relevance**. A paper on "sugar metabolism" scores high for "diabetes treatment" because the embeddings are close — but the intent is completely wrong. A 1987 study and a 2024 RCT score identically despite their difference in evidential weight.

Multi-stage retrieval adds two refinement passes after the initial vector search: a cross-encoder reranker that scores intent alignment, and a document-level fusion step that rewards documents where multiple sections match different aspects of the query.

## The pattern in one diagram

```
QueryAnalysis (from Layer 1)
    │
    ▼  Stage 1: Filtered vector search (broad)
    │  - Search with each query variation's embedding
    │  - Apply metadata filters (date, study_type, section_type)
    │  - Retrieve 50 candidates (deduplicated)
    │
    ▼  Stage 2: Semantic reranking (precise)
    │  - Cross-encoder scores [query, section_title + chunk_text] pairs
    │  - Combine: 0.6 × rerank_score + 0.4 × importance_score
    │  - Trim to top 20
    │
    ▼  Stage 3: Document-level context fusion
    │  - Group chunks by parent document
    │  - Score document = Σ(combined_scores) × (1 + 0.2 × section_diversity)
    │  - Documents with 3 relevant sections from different types outrank single-section hits
    │
    ▼
List[RetrievedDoc] (top_k documents, each with ranked sections)
```

## Phase 1: Filtered vector search across all query variations

Search with every embedding in `QueryAnalysis.embeddings` (one per variation), apply metadata filters, then deduplicate by chunk ID and merge scores.

```python
from collections import defaultdict

def filtered_vector_search(
    query_analysis: QueryAnalysis,
    vectorstore,  # Chroma collection
    limit: int = 50,
) -> list[dict]:
    # Build Chroma where-filter from extracted predicates
    where = _build_where_filter(query_analysis.filters)

    # Search with each variation embedding
    raw_results: list = []
    for embedding in query_analysis.embeddings:
        hits = vectorstore.similarity_search_by_vector(
            embedding, k=limit, filter=where
        )
        raw_results.extend(hits)

    # Deduplicate by chunk_id, keep best score per chunk
    seen: dict[str, dict] = {}
    for hit in raw_results:
        cid = hit.metadata["chunk_id"]
        if cid not in seen or hit.metadata.get("score", 0) > seen[cid].get("score", 0):
            seen[cid] = hit
    return list(seen.values())

def _build_where_filter(filters: dict) -> dict | None:
    conditions = []
    if "date_from" in filters:
        conditions.append({"publication_date": {"$gte": filters["date_from"]}})
    if "study_type" in filters:
        conditions.append({"study_type": {"$eq": filters["study_type"]}})
    if "section_type" in filters:
        conditions.append({"section_type": {"$eq": filters["section_type"]}})
    if not conditions:
        return None
    return {"$and": conditions} if len(conditions) > 1 else conditions[0]
```

**Existing pattern:** `SectionExpandingRetriever._search()` in `src/multivec/components/retrieval/retriever.py` already implements a filter fallback chain: `(doc + section)` → `doc` → unfiltered. Stage 1 sits on top of this by adding multi-embedding search and deduplication.

## Phase 2: Cross-encoder reranking

The cross-encoder sees both the query and the chunk together — far more accurate than cosine similarity, which encodes them independently. Use it to rerank the Stage 1 candidate pool.

```python
from sentence_transformers import CrossEncoder

# Load once at init, not per-query — models are expensive to load
_RERANKER: CrossEncoder | None = None

def get_reranker(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> CrossEncoder:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = CrossEncoder(model_name)
    return _RERANKER

def semantic_rerank(
    query: str,
    candidates: list[dict],
    limit: int = 20,
) -> list[dict]:
    reranker = get_reranker()
    pairs = [
        [query, f"{c.metadata.get('section_name', '')}: {c.page_content}"]
        for c in candidates
    ]
    scores = reranker.predict(pairs)

    for chunk, score in zip(candidates, scores):
        chunk.metadata["rerank_score"] = float(score)
        chunk.metadata["combined_score"] = (
            0.6 * float(score)
            + 0.4 * float(chunk.metadata.get("importance_score", 0.5))
        )

    return sorted(candidates, key=lambda c: c.metadata["combined_score"], reverse=True)[:limit]
```

**Cross-encoder model selection:**
- General: `cross-encoder/ms-marco-MiniLM-L-6-v2` (fast, good quality)
- Scientific: `cross-encoder/ms-marco-MedLM-L-12-v2` (biomedical domain)
- Multilingual: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`

## Phase 3: Document-level context fusion

Single-chunk ranking misses a critical signal: a document where three different sections each partially match your query is almost certainly more relevant than one that has a single very-close chunk. Fusion scores at document level.

```python
from dataclasses import dataclass, field

@dataclass
class RetrievedDoc:
    document_id: str
    document_score: float
    top_section: dict
    all_sections: list[dict]

def context_fusion(
    candidates: list[dict],
    limit: int = 10,
) -> list[RetrievedDoc]:
    # Group chunks by parent document
    by_doc: dict[str, list] = defaultdict(list)
    for chunk in candidates:
        doc_id = chunk.metadata.get("parent_doc_name") or chunk.metadata.get("doc_name")
        by_doc[doc_id].append(chunk)

    # Score each document
    doc_scores: list[RetrievedDoc] = []
    for doc_id, sections in by_doc.items():
        relevance_sum = sum(s.metadata["combined_score"] for s in sections)
        section_types = {s.metadata.get("section_type", "unknown") for s in sections}
        diversity_bonus = 1 + 0.2 * len(section_types)
        score = relevance_sum * diversity_bonus
        top = max(sections, key=lambda s: s.metadata["combined_score"])
        doc_scores.append(RetrievedDoc(doc_id, score, top, sections))

    return sorted(doc_scores, key=lambda d: d.document_score, reverse=True)[:limit]
```

## Full retrieval call

```python
def retrieve(
    query_analysis: QueryAnalysis,
    vectorstore,
    top_k: int = 10,
) -> list[RetrievedDoc]:
    candidates = filtered_vector_search(query_analysis, vectorstore, limit=50)
    reranked   = semantic_rerank(query_analysis.expanded, candidates, limit=20)
    return context_fusion(reranked, limit=top_k)
```

## Relationship to existing retriever

`SectionExpandingRetriever` in `src/multivec/components/retrieval/retriever.py` already implements **section expansion**: chunk hits are expanded to include their full parent section text. This multi-stage layer adds:
- Multi-embedding search (one search per query variation)
- Cross-encoder reranking
- Document-level fusion scoring

You can extend `SectionExpandingRetriever` or wrap it — the expansion step fits naturally between Stage 1 and Stage 2.

## Verification

1. Submit a query known to cause intent confusion with naive vector search — confirm Stage 2 reranking corrects the ranking
2. Submit a query with "recent" — confirm `date_from` filter is applied and pre-2023 chunks don't appear in Stage 1
3. Compare `len(Stage1 results)` ≈ 50, `len(Stage2)` ≈ 20, `len(Stage3)` == `top_k`
4. Inspect a `RetrievedDoc` with `document_score > 2.0` — it should have ≥ 2 sections from different `section_type` values

## Common mistakes

**Mistake 1: Relying on cosine similarity as final rank.**
Vector similarity is a broad filter, not a relevance oracle. Always add at least Stage 2 cross-encoder reranking before returning results to the user.

**Mistake 2: Loading the cross-encoder per query.**
`CrossEncoder` initialization takes 1–3 seconds. Load once at node/application init and cache.

**Mistake 3: Treating all chunks independently.**
A document with one extremely relevant chunk ranks the same as a document where four sections each contribute partial evidence. Stage 3 fusion fixes this by aggregating at document level.

**Mistake 4: No filter fallback chain.**
If a metadata filter returns zero results (e.g., no 2024 papers exist), the query silently returns nothing. Always fall back: `(doc + section)` → `doc` → unfiltered, logging each fallback so you can detect systematic coverage gaps.
