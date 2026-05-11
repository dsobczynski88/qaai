---
name: rag-generation-validation
description: Layer 4 of production RAG architecture. Use when LLM responses hallucinate facts not in source documents, citations are fabricated, or users need a confidence signal to know when to dig deeper. Triggers include "LLM cites sources that don't support the claim", "need to measure hallucination rate", "want claim-level grounding verification", "need confidence score in API response", or "responses sound confident but contain invented facts". Companion skills: rag-context-assembly (Layer 3, produces the context input), rag-document-intelligence (Layer 0, whose section structure enables citation verification). Relates to: BaseLLMNode in src/multivec/components/shared/nodes.py (JSON parsing + Pydantic validation patterns already in this repo).
---

# Layer 4: Generation with Validation

The LLM will hallucinate. This is not a bug to fix — it is a property to engineer around. The generation layer has two responsibilities: (1) constrain the LLM via system prompt to only use provided context, and (2) verify after generation that every factual claim is actually grounded in that context.

The output is not just an answer string — it is a structured response with a confidence score, a per-claim validation result, and source audit metadata.

## The pattern in one diagram

```
AssembledContext  +  QueryAnalysis  (from Layer 3)
    │
    ▼  _generate_answer()
    │  System prompt: citation rules + "never use external knowledge"
    │  User prompt:   context + question
    │  → raw_answer: str (with [Document N, Section] citations)
    │
    ▼  _extract_claims()
    │  LLM call: "list all factual claims in this answer as JSON array"
    │  → claims: list[str]
    │
    ▼  _validate_claims()         ← one LLM call per claim (parallelizable)
    │  "Is this claim explicitly supported by the context? YES/NO/PARTIAL"
    │  → validation: dict[claim, ValidationResult]
    │
    ▼  _build_response()
    │  confidence = validated_count / total_claims
    │  flag: has_gaps = confidence < 0.8
    │
    ▼
ValidatedResponse(answer, confidence_score, validation, sources, metadata)
```

## Phase 1: System prompt with hard citation constraints

The system prompt is the primary defense against hallucination. Make the rules explicit and machine-checkable.

```python
SYSTEM_PROMPT = """You are a research assistant answering questions from provided documents only.

RULES (strictly enforced):
1. Answer ONLY using the documents provided in context. Never use external knowledge.
2. Cite every factual claim using the format [Document N] or [Document N, SECTION_TYPE].
3. If a fact is not in the provided documents, state explicitly: "The provided documents do not address this."
4. If sources contradict each other, note the contradiction rather than resolving it silently.
5. Do not infer, extrapolate, or generalize beyond what is stated in the documents."""
```

**Why rule 3 matters:** An LLM that says "I don't know" when the answer isn't in context is far more useful than one that confidently invents it. Explicit "not found" signals are how users know to seek additional sources.

## Phase 2: Generate answer with structured citations

```python
async def _generate_answer(
    self,
    query: str,
    assembled_context: AssembledContext,
    client,
    model: str,
) -> str:
    user_prompt = f"{assembled_context.context}\n\nQUESTION: {query}"
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.1,   # Low temperature: accuracy over creativity
        max_tokens=1500,
    )
    return response.choices[0].message.content
```

Temperature 0.1 not 0.0 — pure greedy decoding degrades citation formatting on some models.

## Phase 3: Extract factual claims

A separate, fast LLM call (or regex) that pulls discrete factual statements from the answer. These become the unit of validation.

```python
async def _extract_claims(self, answer: str, client, model: str) -> list[str]:
    prompt = (
        f"Extract all factual claims from the answer below.\n"
        f"Return a JSON array of strings. Each string is one atomic claim.\n"
        f"Exclude hedges ('may', 'might'), opinions, and procedural statements.\n\n"
        f"Answer:\n{answer}"
    )
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=500,
    )
    try:
        claims = json.loads(response.choices[0].message.content)
        return claims if isinstance(claims, list) else []
    except json.JSONDecodeError:
        return []  # Validation will score 0 — appropriate signal of uncertainty
```

Use a cheaper/faster model for claim extraction — this is pattern matching, not reasoning.

## Phase 4: Validate each claim against source context

For each claim, verify it can be grounded in the retrieved context. Run these in parallel to control latency.

```python
from pydantic import BaseModel

class ClaimValidation(BaseModel):
    verdict: str          # "YES" | "NO" | "PARTIAL"
    supporting_quote: str # excerpt from context, or "" if NO

async def _validate_one_claim(
    self,
    claim: str,
    context: str,
    client,
    model: str,
) -> ClaimValidation:
    prompt = (
        f"Context (truncated to 3000 chars):\n{context[:3000]}\n\n"
        f"Claim: {claim}\n\n"
        f"Is this claim explicitly supported by the context above?\n"
        f"Answer YES, NO, or PARTIAL. If YES or PARTIAL, quote the supporting text.\n"
        f"Return JSON: {{\"verdict\": \"YES|NO|PARTIAL\", \"supporting_quote\": \"...\"}}"
    )
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=200,
    )
    try:
        data = json.loads(response.choices[0].message.content)
        return ClaimValidation(**data)
    except Exception:
        return ClaimValidation(verdict="NO", supporting_quote="")

async def _validate_claims(
    self,
    claims: list[str],
    context: str,
    client,
    model: str,
) -> dict[str, ClaimValidation]:
    results = await asyncio.gather(
        *[self._validate_one_claim(c, context, client, model) for c in claims]
    )
    return dict(zip(claims, results))
```

## Phase 5: Compute confidence score and build structured response

```python
from dataclasses import dataclass

@dataclass
class ValidatedResponse:
    answer: str
    confidence_score: float        # 0.0 – 1.0
    has_gaps: bool                 # True when confidence < 0.8
    validation: dict[str, ClaimValidation]
    sources: dict                  # documents_used, sections_included, token_count
    metadata: dict                 # validated_claims, total_claims

def _build_response(
    self,
    answer: str,
    validation: dict[str, ClaimValidation],
    assembled_context: AssembledContext,
) -> ValidatedResponse:
    validated = sum(1 for v in validation.values() if v.verdict in ("YES", "PARTIAL"))
    total = len(validation)
    confidence = validated / total if total > 0 else 0.0

    return ValidatedResponse(
        answer=answer,
        confidence_score=round(confidence, 2),
        has_gaps=confidence < 0.8,
        validation=validation,
        sources={
            "documents_used":     assembled_context.documents_used,
            "sections_included":  assembled_context.sections_included,
            "token_count":        assembled_context.token_count,
        },
        metadata={
            "validated_claims": validated,
            "total_claims":     total,
        },
    )
```

**Surface `has_gaps` to users.** When `confidence < 0.8`, prompt the user to verify externally. This is how you build trust — transparency about uncertainty is more valuable than false confidence.

## Existing pattern in this repo

`BaseLLMNode` in `src/multivec/components/shared/nodes.py` already implements:
- `_extract_json_from_markdown()` — strips fences, extracts raw JSON
- `_repair_truncated_json()` — recovers from `max_tokens` cutoffs
- `_parse_llm_response(result, response_model)` — multi-try Pydantic validation with truncation repair fallback

Pydantic models (`Critique`, `Improvement`, `Compliance`) in `src/multivec/components/shared/core.py` all carry `retrieved_chunk_ids: list[str]` — this is the audit trail that enables claim→source tracing, and is the existing repo's lightweight equivalent of claim-level validation.

For new pipelines, build on `BaseLLMNode`'s JSON parsing infrastructure rather than writing raw `json.loads()`. It handles the edge cases (fenced JSON, truncation, repair) that raw parsing misses.

## Full generation call

```python
async def generate(
    self,
    query: str,
    assembled_context: AssembledContext,
    query_analysis: QueryAnalysis,
    client,
    model: str,
) -> ValidatedResponse:
    answer = await self._generate_answer(query, assembled_context, client, model)
    claims = await self._extract_claims(answer, client, model)
    validation = await self._validate_claims(claims, assembled_context.context, client, model)
    return self._build_response(answer, validation, assembled_context)
```

## Verification

1. Submit a query where the answer is clearly in the source — confirm `confidence_score ≥ 0.9`
2. Submit a query asking about something not in any document — confirm `confidence_score = 0.0` and `has_gaps = True`
3. Inspect `validation` dict — each claim should have a `verdict` and non-empty `supporting_quote` for YES verdicts
4. Count `metadata.validated_claims / metadata.total_claims` — should match `confidence_score`
5. Inject a known hallucination by editing the answer — confirm the validator catches it as NO/PARTIAL

## Common mistakes

**Mistake 1: Trusting LLM citations without verification.**
LLMs will write "[Document 3, Results]" when Document 3 contains no such finding. Never display LLM-generated citations to users without running them through `_validate_claims`. The citation is a claim like any other.

**Mistake 2: Claim extraction with the same model and temperature as generation.**
Use a cheaper model at temperature 0 for claim extraction — it's pattern matching. Spending full model capacity on extraction wastes budget and adds latency.

**Mistake 3: Hiding confidence from users.**
A `confidence_score` that only appears in logs is useless for building trust. Surface it in the API response and in any UI. Users who see a 0.6 score know to verify independently; users who see a confident-sounding answer at 0.6 will rely on it incorrectly.

**Mistake 4: Validating claims against the full original corpus, not the assembled context.**
Validate against `assembled_context.context` — the same text the LLM saw. If a claim is grounded in the corpus but not in the assembled context, the LLM didn't have access to it and the citation is invalid for this response.

**Mistake 5: Blocking on sequential claim validation.**
With 10 claims at 300ms each, sequential validation adds 3 seconds. Use `asyncio.gather` to parallelize across claims — the validation LLM calls are independent.
