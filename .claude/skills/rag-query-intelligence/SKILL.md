---
name: rag-query-intelligence
description: Layer 1 of production RAG architecture. Use when retrieval accuracy is low because users write short, ambiguous, or domain-abbreviated queries that the embedding model cannot match to indexed content. Triggers include "users type abbreviations like 'dm + ckd'", "query intent is ambiguous", "need to extract date/type filters from natural language", "query expansion before retrieval", or "vector search returns wrong intent despite similar embeddings". Companion skills: rag-document-intelligence (Layer 0), rag-multi-stage-retrieval (Layer 2).
---

# Layer 1: Query Intelligence

Users don't write queries optimized for vector search. A doctor types "dm + ckd tx" not "diabetes mellitus chronic kidney disease treatment options." A compliance reviewer types "is this ok per FDA?" not "regulatory compliance requirements for adverse event reporting." The embedding model cannot bridge this gap alone.

Query intelligence runs **before** any retrieval call and transforms the raw query into a structured object: expanded text, classified intent, filter predicates, and multiple embedding vectors.

## The pattern in one diagram

```
raw_query: str  ("dm + ckd new tx?")
    │
    ▼  _expand_abbreviations()
expanded: str  ("diabetes mellitus (dm) + chronic kidney disease (ckd) new treatment options?")
    │
    ▼  _classify_intent()          ← LLM or rule-based
intent: str  ("treatment_lookup")
    │
    ├──▶  _generate_variations()   ← 2-3 semantically distinct phrasings
    │     ["therapeutic options for dm + ckd", "clinical management diabetes renal failure"]
    │
    ├──▶  _extract_filters()       ← date, study type, doc type predicates
    │     {date_from: "2023-01-01", study_type: "rct"}
    │
    └──▶  _embed(variation) × N
          [embedding_1, embedding_2, ...]
    │
    ▼
QueryAnalysis(original, expanded, intent, variations, filters, embeddings)
```

## Phase 1: Build your domain abbreviation dictionary

This is the highest-ROI step. A wrong abbreviation expansion poisons all downstream retrieval. Build the dictionary manually for your domain — do not rely on an LLM to expand abbreviations at query time (too slow, non-deterministic).

```python
# Domain-specific — customize per project
ABBREVIATIONS: dict[str, str] = {
    # Medical
    "dm":  "diabetes mellitus",
    "ckd": "chronic kidney disease",
    "htn": "hypertension",
    "mi":  "myocardial infarction",
    # Regulatory
    "sop": "standard operating procedure",
    "ae":  "adverse event",
    "capa":"corrective and preventive action",
    # Add your domain's terms here
}

def expand_abbreviations(query: str) -> str:
    expanded = query.lower()
    for abbr, full in ABBREVIATIONS.items():
        # Replace whole-word abbreviations only
        expanded = re.sub(rf"\b{re.escape(abbr)}\b", f"{abbr} ({full})", expanded)
    return expanded
```

## Phase 2: Classify query intent

Intent classification lets you apply intent-specific query variations and retrieval strategies downstream.

**Rule-based (fast, predictable):**
```python
INTENT_KEYWORDS: dict[str, list[str]] = {
    "treatment_lookup":  ["treatment", "therapy", "drug", "tx", "intervention"],
    "diagnosis_support": ["diagnos", "symptom", "present", "sign"],
    "recent_research":   ["recent", "latest", "new", "2024", "2023", "updated"],
    "guideline_check":   ["guideline", "recommend", "fda", "sop", "protocol", "comply"],
    "drug_interaction":  ["interaction", "contraindic", "combination", "with"],
}

def classify_intent(expanded_query: str) -> str:
    q = expanded_query.lower()
    scores = {intent: sum(1 for kw in kws if kw in q)
              for intent, kws in INTENT_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"
```

**LLM-based (more accurate, adds latency):** Use only when the domain is complex enough that keyword matching fails. Cache results by query hash to avoid redundant LLM calls.

## Phase 3: Generate query variations

Two to three variations per query. Too few — you miss synonymous phrasing. Too many — embedding cost explodes and later fusion becomes noisy.

```python
INTENT_VARIATIONS: dict[str, list[str]] = {
    "treatment_lookup":  [
        "therapeutic options for {query}",
        "clinical management of {query}",
    ],
    "recent_research":   [
        "latest studies on {query}",
        "recent findings about {query}",
    ],
    "guideline_check":   [
        "regulatory requirements for {query}",
        "compliance standards {query}",
    ],
}

def generate_variations(expanded: str, intent: str) -> list[str]:
    templates = INTENT_VARIATIONS.get(intent, [])
    return [expanded] + [t.format(query=expanded) for t in templates]
```

## Phase 4: Extract metadata filters from natural language

Surface retrieval predicates hidden in the query text. These become Chroma `where` filters in Layer 2, dramatically narrowing the candidate set before vector similarity runs.

```python
def extract_filters(expanded_query: str) -> dict:
    filters: dict = {}
    q = expanded_query.lower()

    # Date recency
    if any(w in q for w in ["recent", "latest", "new", "2024", "2023"]):
        filters["date_from"] = "2023-01-01"

    # Study type
    if "meta-analysis" in q or "systematic review" in q:
        filters["study_type"] = "meta_analysis"
    elif "randomized" in q or " rct" in q:
        filters["study_type"] = "rct"

    # Section type targeting (domain-specific)
    if "abstract" in q:
        filters["section_type"] = "abstract"
    elif "result" in q or "finding" in q:
        filters["section_type"] = "results"

    return filters
```

## Phase 5: Assemble the QueryAnalysis object

Return a typed object, not a dict. Downstream layers (retriever, context assembler) depend on this contract.

```python
from dataclasses import dataclass, field

@dataclass
class QueryAnalysis:
    original: str
    expanded: str
    intent: str
    variations: list[str]
    filters: dict
    embeddings: list[list[float]] = field(default_factory=list)

def analyze_query(raw_query: str, embed_fn) -> QueryAnalysis:
    expanded = expand_abbreviations(raw_query)
    intent = classify_intent(expanded)
    variations = generate_variations(expanded, intent)
    filters = extract_filters(expanded)
    embeddings = [embed_fn(v) for v in variations]
    return QueryAnalysis(raw_query, expanded, intent, variations, filters, embeddings)
```

## Existing pattern in this repo

`_EvalNodeBase._retrieve_source_context()` in `src/multivec/components/shared/nodes.py` constructs a two-part query:
```python
query = f"{comment.doc_section_name} :: {comment.comment_body}"
```
This is a lightweight form of query enrichment — prepending the section name grounds the embedding in the document structure. A full query intelligence layer would additionally expand abbreviations, classify intent, and extract date/type filters before this call.

## Verification

1. Feed a query with known abbreviations — confirm expansion before any retrieval call is made
2. Feed "latest studies on X" — confirm `filters["date_from"]` is set and only post-2023 chunks are retrieved
3. Feed "what does the SOP say about Y" — confirm `intent == "guideline_check"` and variation includes "regulatory requirements"
4. Check embedding count: `len(analysis.embeddings) == len(analysis.variations)` always

## Common mistakes

**Mistake 1: Query intelligence after retrieval.**
Some teams run retrieval first, then "fix" the results. This is backwards. The query must be enriched before any vector search — once you've retrieved the wrong candidates, reranking cannot recover.

**Mistake 2: Too many variations.**
Generating 10 variations for every query means 10× the embedding cost and a noisy candidate set. Two to three is the right range for most domains.

**Mistake 3: LLM intent classification without caching.**
LLM calls for intent add 200–800ms per query. Cache by `hash(raw_query)` or use rule-based classification — intent categories are usually stable enough for keywords.

**Mistake 4: Treating abbreviation expansion as optional.**
In specialized domains, a single unexpanded abbreviation causes completely wrong retrieval. "DM" in a computer hardware corpus means "direct memory" not "diabetes mellitus." Build the dictionary for your specific domain before any other work.
