"""Component registry + live run+score execution.

Maps ``spec.component`` to the compiled LangGraph runnable, an input-state builder
(mirrors qaai/api/services.py), and the completeness predicate. Heavy imports
(langgraph, the reviewer packages) are lazy so score-only mode never pays for them.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

EVAL_CACHE_MODE = "off"  # eval always re-runs; never reuse cached interim results


# ── Per-component input builders (row dict -> graph input state) ──────────────

def _rtm_input(row: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    from qaai.agents.shared.core import Requirement, TestCase, DesignDocument

    req = row.get("requirement")
    state: Dict[str, Any] = {
        "requirement": Requirement(**req) if isinstance(req, dict) else req,
        "test_cases": [TestCase(**tc) if isinstance(tc, dict) else tc for tc in row.get("test_cases", [])],
        "cache_mode": cache_mode,
    }
    if row.get("design_docs"):
        state["design_docs"] = [DesignDocument(**d) if isinstance(d, dict) else d for d in row["design_docs"]]
    return state


def _tc_input(row: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    from qaai.agents.shared.core import Requirement, TestCase, DesignDocument

    tc = row.get("test_case")
    state: Dict[str, Any] = {
        "test_case": TestCase(**tc) if isinstance(tc, dict) else tc,
        "requirements": [Requirement(**r) if isinstance(r, dict) else r for r in row.get("requirements", [])],
        "design_docs": [DesignDocument(**d) if isinstance(d, dict) else d for d in row.get("design_docs", [])],
        "cache_mode": cache_mode,
    }
    return state


def _hazard_input(row: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    from qaai.agents.hazard_risk_reviewer.core import HazardRowWithTraceMatrix

    hazard = row.get("hazard", row)
    hz = HazardRowWithTraceMatrix(**hazard) if isinstance(hazard, dict) else hazard
    return {"hazard": hz, "cache_mode": cache_mode}


@dataclass
class Component:
    runnable_import: Callable[[], Any]
    build_input: Callable[[Dict[str, Any], str], Dict[str, Any]]
    is_complete_import: Callable[[], Callable[[dict], bool]]


COMPONENTS: Dict[str, Component] = {
    "test_suite_reviewer": Component(
        runnable_import=lambda: __import__(
            "qaai.agents.test_suite_reviewer.pipeline", fromlist=["RTMReviewerRunnable"]
        ).RTMReviewerRunnable,
        build_input=_rtm_input,
        is_complete_import=lambda: __import__("qaai.api.services", fromlist=["rtm_is_complete"]).rtm_is_complete,
    ),
    "test_case_reviewer": Component(
        runnable_import=lambda: __import__(
            "qaai.agents.test_case_reviewer.pipeline", fromlist=["TCReviewerRunnable"]
        ).TCReviewerRunnable,
        build_input=_tc_input,
        is_complete_import=lambda: __import__("qaai.api.services", fromlist=["tc_is_complete"]).tc_is_complete,
    ),
    "hazard_risk_reviewer": Component(
        runnable_import=lambda: __import__(
            "qaai.agents.hazard_risk_reviewer.pipeline", fromlist=["HazardReviewerRunnable"]
        ).HazardReviewerRunnable,
        build_input=_hazard_input,
        is_complete_import=lambda: __import__("qaai.api.services", fromlist=["hazard_is_complete"]).hazard_is_complete,
    ),
}


def build_client(allow_prod: bool = False, telemetry_tracker: Any = None) -> Tuple[Any, str]:
    """Construct the rate-limited client from settings; guard against prod URLs."""
    from qaai.core.config import settings
    from qaai.agents.clients import RateLimitOpenAIClient

    base = settings.url or ""
    if not allow_prod and "prod" in base.lower():
        raise SystemExit(
            f"Refusing to evaluate against a base_url containing 'prod' ({base!r}). "
            f"Pass --allow-prod to override."
        )
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        base_url=settings.url,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
        telemetry_tracker=telemetry_tracker,
    )
    return client, settings.model


async def run_and_collect(
    component: str,
    inputs: Sequence[Dict[str, Any]],
    *,
    client: Any,
    model: str,
    prompt_set: Optional[str],
    cache_manager: Any = None,
    max_concurrent: int = 10,
    cache_mode: str = EVAL_CACHE_MODE,
) -> Tuple[List[Optional[dict]], List[float], List[bool], List[Optional[str]]]:
    """Invoke the graph on every input row (bounded concurrency); collect ordered results."""
    from langgraph.checkpoint.memory import MemorySaver
    from qaai.core.config import PromptConfig

    comp = COMPONENTS[component]
    Runnable = comp.runnable_import()
    is_complete = comp.is_complete_import()
    prompt_config = PromptConfig.from_set(prompt_set) if prompt_set else None
    runnable = Runnable(
        client, model, checkpointer=MemorySaver(),
        prompt_config=prompt_config, cache_manager=cache_manager,
    )

    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(i: int, row: Dict[str, Any]):
        async with sem:
            t0 = time.perf_counter()
            try:
                state = comp.build_input(row, cache_mode)
                out = await runnable.graph.ainvoke(state, {"configurable": {"thread_id": f"eval-{i:04d}"}})
                return i, out, time.perf_counter() - t0, bool(is_complete(out)), None
            except Exception as e:  # soft-fail one record, keep the batch alive
                return i, None, time.perf_counter() - t0, False, repr(e)

    results = await asyncio.gather(*(run_one(i, row) for i, row in enumerate(inputs)))
    results.sort(key=lambda x: x[0])
    outputs = [r[1] for r in results]
    latencies = [r[2] for r in results]
    completes = [r[3] for r in results]
    errors = [r[4] for r in results]
    return outputs, latencies, completes, errors
