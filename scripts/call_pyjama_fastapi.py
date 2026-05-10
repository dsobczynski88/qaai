import json

import asyncio
from pathlib import Path

from dotenv import load_dotenv
from py_jama_rest_client.client import JamaClient

import src
from src.jama.pyjamaapi import PyJamaAPI
from src.utils import gen_utils, jama_utils

from autoqa.prj_logger import ProjectLogger
from autoqa.core.config import settings

load_dotenv()

# Config
config = gen_utils.yaml_loader(config_file='config.yaml')
host_address = config['host_address']
data_path = config['data_path']
json_input_file = "tests/fixtures/jama_reviews.jsonl"

# Input JSONL file (one JSON object per line)
# Example list of dictionaries (each will be one line in JSONL)
jama_reviews = [
    {
        "project_name": config["project_name"],
        "review_name": config["review_name"],

    }   
]

with open(json_input_file, "w", encoding="utf-8") as f:
    for review in jama_reviews:
        f.write(json.dumps(review, ensure_ascii=False) + "\n")

# Auth
credentials = jama_utils.get_jama_credentials()
jama_client = JamaClient(host_address, credentials, oauth=True, verify=True)

# Build and write JSONL outputs using file-driven input mode
pyjama_api = PyJamaAPI(
    jama_client=jama_client,
    data_path=data_path,
    project_name=None,
    review_name=None,
    json_input_file=json_input_file,
)

# need to fix this class so logging location can be updated
#pyjama_api._log_dir = settings.log_file_path

results = asyncio.run(pyjama_api.run())

# Save each payload with formatted filename
def sanitize_filename(name: str) -> str:
    """Replace spaces and special characters with underscores for valid filenames."""
    return name.replace(" ", "_").replace("/", "_").replace("\\", "_")

for result in results:
    project_name = result.get("project_name", "unknown_project")
    review_name = result.get("review_name", "unknown_review")
    payload = result.get("payload", [])
    
    # Sanitize names for filename
    safe_project = sanitize_filename(project_name)
    safe_review = sanitize_filename(review_name)
    
    # Create filename
    output_filename = f"inputs_{safe_project}_{safe_review}.jsonl"
    output_path = Path(pyjama_api.log_dir) / output_filename
    
    # Write payload to JSONL file
    with open(output_path, "w", encoding="utf-8") as f:
        for item in payload:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    print(f"Saved payload for {project_name} / {review_name} to {output_path}")

print(f"Done — {len(results)} project/review task result(s) processed")
print(f"All files written to {pyjama_api.log_dir}")