"""Bidirectional Trace example — node-based, mirrors Bidirectional_Trace.md.

For each input requirement, fetches the full bidirectional trace: the upstream
hierarchy (system requirements -> user needs) AND the downstream verification
artifacts (test cases + design documents), via PyJamaDataSourceNode. Keyed by a
project name + a list of identifiers (GIDs or document keys).

The node returns the raw structure in ``jama_data`` (no transform helper).

Requires a `.env` with JAMA_CLIENT_ID, JAMA_CLIENT_SECRET, and JAMA_HOST_ADDRESS.
With cache_mode=USE the first run writes one file per identifier under
./cache/source/identifiers/ and subsequent runs are served from disk.

Run:
    uv run python examples/bidirectional_trace_from_gids.py [GID-1 GID-2 ...]
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig, PyJamaRequest
from pyjama.utils.cache_manager import CacheMode


# Replace with your project name (or set it here once).
PROJECT_NAME = "Patient Safety Platform"
DEFAULT_IDENTIFIERS = ["REQ-PUMP-101"]


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


async def main(project_name: str, identifiers: list[str]) -> None:
    node = build_node()

    request = PyJamaRequest(
        request_type="bidirectional_trace",
        project_name=project_name,   # required for this request type
        identifiers=identifiers,     # GIDs or document keys
    )

    print(f"Fetching bidirectional trace for {len(identifiers)} identifiers")
    print(f"Project: {project_name}")
    result = await node({"pyjama_request": request})

    jama_data = result["jama_data"]   # list, one entry per input requirement
    meta = result["jama_metadata"]    # {request_type, count, project_name, identifiers}

    print(f"\n{meta['count']} software requirements")
    for req in jama_data:
        print(
            f"  {req['requirement']['req_id']} | "
            f"upstream sys reqs: {len(req['system_requirements'])} | "
            f"downstream tests: {len(req['test_cases'])} | "
            f"design docs: {len(req['design_docs'])}"
        )


if __name__ == "__main__":
    # Optional override: pass one or more identifiers as arguments.
    ids = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_IDENTIFIERS
    asyncio.run(main(PROJECT_NAME, ids))
