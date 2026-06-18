"""Test Suite Reviewer example — node-based, mirrors README_test_suite_reviewer.md.

Fetches a requirement-centric structure from a Jama baseline: every requirement
with its downstream test cases and design documents, via PyJamaDataSourceNode.

Requires a `.env` with JAMA_CLIENT_ID, JAMA_CLIENT_SECRET, and JAMA_HOST_ADDRESS.
With cache_mode=USE the first run writes ./cache/source/baselines/<id>/ and
subsequent runs are served from disk. This baseline request keys off a baseline_id
and never resolves a project name, so it does not create ./cache/source/projects/
(only project_name-based requests like bidirectional_trace do).

Set PYJAMA_TEST_MODE=true to run strictly from the seeded disk cache: no
JamaClient is built and the Jama API is never contacted, so mock/absent
credentials are fine. A request whose baseline_id is not cached raises
CacheMissError.

Run:
    uv run python examples/test_suite_reviewer_example.py [BASE-12345]
    # cache-only, with mock or no credentials:
    PYJAMA_TEST_MODE=true uv run python examples/test_suite_reviewer_example.py BASE-12345
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig, PyJamaRequest
from pyjama.utils.cache_manager import CacheMode


def build_node() -> PyJamaDataSourceNode:
    """Build a PyJamaDataSourceNode from environment variables."""
    load_dotenv()
    config = PyJamaNodeConfig(
        host_address=os.getenv("JAMA_HOST_ADDRESS"),
        client_id=os.getenv("JAMA_CLIENT_ID"),
        client_secret=os.getenv("JAMA_CLIENT_SECRET"),
        max_concurrent=100,
        # log_path="logs",  # base dir for logs; a timestamped run-<ts>/ is created inside
        cache_mode=CacheMode.USE,  # OFF / USE / REFRESH
        test_mode="false"#os.getenv("PYJAMA_TEST_MODE", "false").lower() == "true",
    )
    return PyJamaDataSourceNode(config)


async def main(baseline_id: str) -> None:
    node = build_node()

    request = PyJamaRequest(
        request_type="test_suite_review",
        baseline_id=baseline_id,  # required for this request type
    )

    print(f"Fetching test suite reviewer structure for baseline: {baseline_id}")
    result = await node({"pyjama_request": request})

    jama_data = result["jama_data"]   # list, one entry per requirement
    meta = result["jama_metadata"]    # {request_type, count, baseline_id, ...}

    print(f"\n{meta['count']} requirements")
    for req in jama_data:
        print(
            f"  {req['requirement']['req_id']}: "
            f"{len(req['test_cases'])} tests, "
            f"{len(req['design_docs'])} design docs"
        )


if __name__ == "__main__":
    # Optional override: pass a baseline id as the first argument.
    #baseline = sys.argv[1] if len(sys.argv) > 1 else "BASE-12345"
    
    baselines = [
        {"baseline_id": "BASE-43796"},
        #{"baseline_id": "BASE-34605"},
        #{"baseline_id": "BASE-58306"},
        #{"baseline_id": "BASE-32133"},
        #{"baseline_id": "BASE-84930"},
    ]

    for bl in baselines:
        asyncio.run(main(bl.get("baseline_id")))
