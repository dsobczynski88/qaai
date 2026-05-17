from pyjama.jama import PyJamaTraceMatrix
from py_jama_rest_client.client import JamaClient
from dotenv import load_dotenv
import os

# Load credentials
load_dotenv()
client_id = os.getenv("JAMA_CLIENT_ID")
client_secret = os.getenv("JAMA_CLIENT_SECRET")
host_address = "https://baxter-international.jamacloud.com/"

# Initialize Jama client
jama_client = JamaClient(
    host_domain=host_address,
    credentials=(client_id, client_secret),
    oauth=True
)

# Create PyJamaTraceMatrix instance
api = PyJamaTraceMatrix(
    jama_client=jama_client,
    data_path="./data",
    log_path="logs",
    max_concurrent=100
)

# Extract test suite reviewer structure from a baseline
result = api.get_test_suite_reviewer_structure(
    baseline_id="BASE-84429"
)

print(f"Found {len(result)} requirements")
for req in result:
    req_id = req['requirement']['req_id']
    test_count = len(req['test_cases'])
    design_count = len(req['design_docs'])
    print(f"  {req_id}: {test_count} tests, {design_count} design docs")