"""Test Case Reviewer example — node-based, mirrors README_test_case_reviewer.md.

Fetches a test-case-centric structure from a Jama baseline: every test case with
its upstream requirements and design documents (the inverse of the test suite
reviewer), via PyJamaDataSourceNode.

Requires a `.env` with JAMA_CLIENT_ID, JAMA_CLIENT_SECRET, and JAMA_HOST_ADDRESS.
With cache_mode=USE the first run writes ./cache/source/baselines/<id>/ and
subsequent runs are served from disk. This baseline request keys off a baseline_id
and never resolves a project name, so it does not create ./cache/source/projects/
(only project_name-based requests like bidirectional_trace do).

Run:
    uv run python examples/test_case_reviewer_example.py [BASE-12345]
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
        test_mode=os.getenv("PYJAMA_TEST_MODE", "false").lower() == "true",
    )
    return PyJamaDataSourceNode(config)


async def main(baseline_id: str) -> None:
    node = build_node()

    request = PyJamaRequest(
        request_type="test_case_review",
        baseline_id=baseline_id,  # required for this request type
    )

    print(f"Fetching test case reviewer structure for baseline: {baseline_id}")
    result = await node({"pyjama_request": request})

    jama_data = result["jama_data"]   # list, one entry per test case
    meta = result["jama_metadata"]    # {request_type, count, baseline_id, ...}

    print(f"\n{meta['count']} test cases")
    for tc in jama_data:
        print(
            f"  {tc['test_case']['test_id']}: "
            f"{len(tc['requirements'])} requirements, "
            f"{len(tc['design_docs'])} design docs"
        )


if __name__ == "__main__":
    # Optional override: pass a baseline id as the first argument.
    baseline = sys.argv[1] if len(sys.argv) > 1 else "BASE-12345"
    asyncio.run(main(baseline))
