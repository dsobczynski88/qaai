---
name: rag-context-assembly
description: Layer 3 of production RAG architecture. Use when retrieved chunks are correct but LLM responses are generic, hallucinated, or fail to cite sources — meaning retrieval is working but context is being fed to the LLM poorly. Triggers include "LLM ignores retrieved content", "context window overflows with irrelevant chunks", "no token budget management per document", "want structured context with source metadata", or "need to control which sections get included within a token limit". Companion skills: rag-multi-stage-retrieval (Layer 2, produces RetrievedDoc input), rag-generation-validation (Layer 4, consumes assembled context).
---

# Layer 3: Context Assembly

Retrieving the right chunks is necessary but not sufficient. Dumping all retrieved text into the prompt as a flat string is the second most common failure point in RAG systems — after bad retrieval. The LLM can't distinguish high-importance results from low-importance ones, can't identify source provenance, and overflows its useful context window with noise.

Context assembly is the deliberate construction of a structured prompt context that:
1. Fits within a token budget
2. Presents documents with structured metadata headers (source credibility signals)
3. Prioritizes sections by combined relevance + importance score
4. Includes only sections that actually help — drops the rest

## The pattern in one diagram

```
List[RetrievedDoc]  (from Layer 2)   +   QueryAnalysis  (from Layer 1)
    │
    ▼  _allocate_token_budget()
    │  global_budget = 6000 tokens; per_doc_budget = 1500 tokens
    │
    ▼  For each RetrievedDoc (ordered by document_score desc):
    │    _create_document_header()    → "[DOCUMENT 1]\nTitle: ...\nYear: ...\nScore: ...\n---"
    │    _select_sections()           → pick highest combined_score sections within per_doc_budget
    │    _build_document_block()      → header + [RESULTS]\n...\n[ABSTRACT]\n...
    │    Stop when global_budget exhausted
    │
    ▼  _structure_context()
    │  "Query Intent: treatment_lookup\nExpanded: ...\n\nRetrieved N documents:\n[DOCUMENT 1]..."
    │
    ▼
AssembledContext(context_str, token_count, documents_used, sections_included)
```

## Phase 1: Define token budgets

Always reserve tokens for the LLM's response. The assembled context must leave breathing room.

```python
import tiktoken

class ContextAssembler:
    def __init__(self, model: str = "gpt-4", response_reserve: int = 1500):
        self.enc = tiktoken.encoding_for_model(model)
        # For Bedrock/local models without tiktoken support, use word-count approximation:
        # tokens ≈ len(text.split()) * 1.3
        self.max_context_tokens = 8192 - response_reserve  # 6700 for gpt-4
        self.max_tokens_per_doc = 1500

    def _count_tokens(self, text: str) -> int:
        return len(self.enc.encode(text))
```

## Phase 2: Build document headers with source metadata

The LLM uses document headers to judge source credibility and apply appropriate citation labels. This is how you avoid fabricated citations — the LLM can reference "[DOCUMENT 2]" and your validator can verify it.

```python
def _create_document_header(self, doc: RetrievedDoc, index: int) -> str:
    meta = doc.top_section.metadata
    return (
        f"[DOCUMENT {index}]\n"
        f"Title: {meta.get('paper_title') or meta.get('doc_name', 'Unknown')}\n"
        f"Authors: {meta.get('authors', 'Unknown')}\n"
        f"Year: {meta.get('year', 'Unknown')}\n"
        f"Study Type: {meta.get('study_type', 'Unknown')}\n"
        f"Relevance Score: {doc.document_score:.2f}\n"
        f"---"
    )
```

Minimum useful fields: **document identifier** (for citation) + **year/recency** (for temporal grounding) + **relevance score** (so LLM knows which document is most applicable).

## Phase 3: Select sections within per-document budget

Don't include all sections from a retrieved document — select the highest-scoring ones that fit within `max_tokens_per_doc`.

```python
def _select_sections(self, doc: RetrievedDoc, global_used: int) -> list:
    # Sort sections: highest combined_score first
    sorted_sections = sorted(
        doc.all_sections,
        key=lambda s: (
            s.metadata.get("importance_score", 0.5) * 0.4
            + s.metadata.get("rerank_score", 0.0) * 0.6
        ),
        reverse=True,
    )

    selected = []
    doc_tokens = 0
    for section in sorted_sections:
        text_tokens = self._count_tokens(section.page_content)
        if doc_tokens + text_tokens > self.max_tokens_per_doc:
            continue  # Try next section (smaller ones may still fit)
        if global_used + doc_tokens + text_tokens > self.max_context_tokens:
            return selected  # Global budget exhausted
        selected.append(section)
        doc_tokens += text_tokens
    return selected
```

Note: `continue` (not `break`) — a long results section might overflow but the abstract still fits.

## Phase 4: Format section blocks with type labels

Label each section with its type so the LLM understands what it's reading. Use consistent uppercase tags that are easy to cite in responses.

```python
def _build_document_block(self, header: str, sections: list) -> str:
    parts = [header]
    for section in sections:
        section_type = section.metadata.get("section_type", "CONTENT").upper()
        parts.append(f"\n[{section_type}]\n{section.page_content}")
    return "\n".join(parts)
```

## Phase 5: Structure the full context with a query header

Prepend a query context block so the LLM knows the intent before reading the documents. This dramatically improves citation accuracy and reduces the chance of the LLM answering a different question.

```python
def _structure_context(
    self,
    query_analysis: QueryAnalysis,
    blocks: list[str],
) -> str:
    header = (
        f"Query Intent: {query_analysis.intent}\n"
        f"Expanded Query: {query_analysis.expanded}\n\n"
        f"Retrieved {len(blocks)} relevant document(s):\n"
    )
    return header + "\n\n".join(blocks)
```

## Full assembly call

```python
from dataclasses import dataclass

@dataclass
class AssembledContext:
    context: str
    token_count: int
    documents_used: int
    sections_included: int

def assemble_context(
    self,
    query_analysis: QueryAnalysis,
    retrieved_docs: list[RetrievedDoc],
) -> AssembledContext:
    blocks = []
    global_tokens = self._count_tokens(
        f"Query Intent: {query_analysis.intent}\nExpanded Query: {query_analysis.expanded}\n\n"
    )
    sections_total = 0

    for i, doc in enumerate(retrieved_docs, 1):
        header = self._create_document_header(doc, i)
        sections = self._select_sections(doc, global_tokens)
        if not sections:
            continue
        block = self._build_document_block(header, sections)
        block_tokens = self._count_tokens(block)
        blocks.append(block)
        global_tokens += block_tokens
        sections_total += len(sections)

    context = self._structure_context(query_analysis, blocks)
    return AssembledContext(context, global_tokens, len(blocks), sections_total)
```

## Existing pattern in this repo

Jinja2 templates in `src/prompts/` (`critique-v3.jinja2`, `extract-insights-v2.jinja2`, etc.) assemble context from retrieved text. The `_EvalNodeBase._build_prompt_and_meta()` in `src/multivec/components/shared/nodes.py` does:
```python
ctx, chunk_ids = self.retriever.retrieve_with_ids(query, doc_name=..., section_name=...)
prompt = tmpl.render(source_context=ctx, comment_data=comment.model_dump())
```
This repo's approach is section-expanding retrieval + Jinja rendering. Layer 3 extends this by adding token budget management and structured document headers with metadata — the Jinja template would receive a pre-assembled `AssembledContext.context` string rather than raw retriever output.

## Verification

1. Assemble context for a large query — confirm `token_count < max_context_tokens`
2. Verify `[DOCUMENT 1]` header appears in output with correct year/score
3. Verify section labels like `[RESULTS]`, `[ABSTRACT]` appear within document blocks
4. Force a case where all documents together exceed budget — confirm graceful truncation (no `IndexError`)
5. Confirm `sections_included < sum(len(d.all_sections) for d in retrieved_docs)` — some sections should be dropped

## Common mistakes

**Mistake 1: No per-document token budget.**
Without `max_tokens_per_doc`, a single very-long document consumes the entire context, leaving nothing for other retrieved documents. Budget per document, not just globally.

**Mistake 2: Omitting source metadata from headers.**
Without `[DOCUMENT 1]` headers and year/type metadata, the LLM cannot form grounded citations. It will either omit citations or fabricate them. Document identity in the context is prerequisite for citation validation in Layer 4.

**Mistake 3: Sorting context by chunk score, not document score.**
Chunks from the same document should be grouped. Interleaving chunks from different documents breaks the LLM's ability to reason about a single source's argument.

**Mistake 4: Using `break` instead of `continue` in section selection.**
If section A is too long but section B fits, breaking exits — you lose section B. Continue to check all remaining sections.
