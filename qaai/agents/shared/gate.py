"""
Shared input-validation gate for reviewer graphs.

Every reviewer graph runs a small ``validation_gate`` node directly after the
``transform`` node. The gate inspects the in-state inputs a reviewer needs to
produce a meaningful result. When required inputs are missing it short-circuits
the graph straight to ``END`` — so **no LLM/inference calls are made** — and
records why on the state:

    {
        "review_status": "skipped",
        "skip_reason":   "<human-readable summary>",
        "missing_fields": ["<label>", ...],
    }

These three fields ride through to ``outputs.jsonl`` and are read per-record by
the viewer (``qaai/viewer/common/shared.js``) to render the missing-fields
warning banner + details modal. When all required inputs are present the gate is
a no-op (returns ``{}``) and the graph proceeds normally.

Usage in a ``pipeline.py``::

    from qaai.agents.shared.gate import make_validation_gate, make_gate_router

    sg.add_node("validation_gate", make_validation_gate(validate_my_inputs))
    sg.add_edge("transform", "validation_gate")
    sg.add_conditional_edges(
        "validation_gate",
        make_gate_router(["decomposer", "summarizer"]),
        ["decomposer", "summarizer", END],
    )

``validate_fn(state) -> list[str]`` returns the labels of missing/invalid
inputs (empty list ⇒ valid).
"""
import logging
from typing import Any, Callable, Dict, List, Union

from langgraph.graph import END

logger = logging.getLogger(__name__)

# Value written to state["review_status"] when the gate stops a graph early.
SKIP_STATUS = "skipped"

ValidateFn = Callable[[Dict[str, Any]], List[str]]


def _skip_reason(missing: List[str]) -> str:
    """Human-readable summary embedded in the viewer warning's details modal."""
    return (
        "Required fields were missing from this record: "
        + ", ".join(missing)
        + ". The review was unable to complete successfully."
    )


def make_validation_gate(validate_fn: ValidateFn):
    """Build a gate node that flags missing required inputs.

    ``validate_fn`` returns a list of missing-input labels. When non-empty the
    node marks the record skipped (the router then sends it to END); otherwise
    it returns ``{}`` so the graph continues unchanged.
    """

    def validation_gate(state: Dict[str, Any]) -> Dict[str, Any]:
        missing = validate_fn(state) or []
        if missing:
            logger.warning(
                "validation_gate: skipping review — missing required inputs: %s",
                ", ".join(missing),
            )
            return {
                "review_status": SKIP_STATUS,
                "skip_reason": _skip_reason(missing),
                "missing_fields": missing,
            }
        return {}

    return validation_gate


def make_gate_router(normal_targets: List[str]):
    """Build the conditional-edge router for the gate.

    Routes to ``END`` when the gate marked the record skipped, otherwise fans
    out to ``normal_targets`` (returning a list triggers parallel branches in
    LangGraph, matching the original ``transform -> [node, ...]`` fan-out).
    """

    def route_after_gate(state: Dict[str, Any]) -> Union[str, List[str]]:
        if state.get("review_status") == SKIP_STATUS:
            return END
        return normal_targets

    return route_after_gate
