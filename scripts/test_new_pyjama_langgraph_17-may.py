import os
import asyncio
from dotenv import load_dotenv
from pyjama.langgraph.nodes import (
    PyJamaDataSourceNode,
    PyJamaNodeConfig,
    PyJamaRequest,
)
from pyjama.langgraph.transforms import transform_test_suite_review_to_state

# Load credentials
load_dotenv()

# Configure PyJama node
config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET"),
    max_concurrent=100
)

# Create node
jama_node = PyJamaDataSourceNode(config)

# Create request
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84429"
)

# Fetch data
async def run():
    return await jama_node({"pyjama_request": request})
    
result = asyncio.run(run())

print(result)
# Transform to LangGraph state format
#states = transform_test_suite_review_to_state(result["jama_data"])

# Process each requirement through your graph
#for state in states:
#    graph_result = await your_graph.ainvoke(state)