from pathlib import Path
import json
from unittest.mock import MagicMock, AsyncMock
from pydantic import BaseModel
from autoqa.components.clients import RateLimitOpenAIClient

def resolve_fixture_path(fixture_name: str) -> Path:
    """Resolve a fixture filename to a concrete path under tests/fixtures/.

    Searches in the following subdirectories in order:
    1. mock/ - Mock LLM responses for unit tests
    2. gold/ - Canonical labeled datasets for evaluation
    3. local/ - Project-specific converted/derived fixtures
    4. external/ - Third-party or reference datasets
    5. Root fixtures/ directory (for backwards compatibility)

    Works for any file type (e.g. .jsonl or .xlsx) since it only resolves the
    path and does not parse the file.

    Args:
        fixture_name: Name of the fixture file (e.g., 'decomposer_cases.jsonl'
            or 'software_hazard_analysis.xlsx')

    Returns:
        Path to the first matching fixture file.

    Raises:
        FileNotFoundError: If fixture file not found in any search path
    """
    fixtures_root = Path(__file__).parent / "fixtures"
    search_paths = [
        fixtures_root / "mock" / fixture_name,
        fixtures_root / "gold" / fixture_name,
        fixtures_root / "local" / fixture_name,
        fixtures_root / "external" / fixture_name,
        fixtures_root / fixture_name,  # Backwards compatibility
    ]

    for path in search_paths:
        if path.exists():
            return path

    # If not found, raise with helpful message
    raise FileNotFoundError(
        f"Fixture '{fixture_name}' not found in any of: "
        f"{', '.join(str(p.parent.name) + '/' for p in search_paths[:-1])} or root fixtures/"
    )


def load_jsonl(fixture_name: str) -> list[dict]:
    """Load test cases from a JSONL fixture file in tests/fixtures/.

    Uses resolve_fixture_path() for the search order (mock -> gold -> local ->
    external -> root fixtures/).

    Args:
        fixture_name: Name of the fixture file (e.g., 'decomposer_cases.jsonl')

    Returns:
        List of dictionaries parsed from JSONL file

    Raises:
        FileNotFoundError: If fixture file not found in any search path
    """
    path = resolve_fixture_path(fixture_name)
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def serialize_state(state: dict) -> dict:
    """Convert an RTMReviewState dict to a JSON-serializable dict."""
    out = {}
    for key, value in state.items():
        if isinstance(value, BaseModel):
            out[key] = value.model_dump()
        elif isinstance(value, list):
            out[key] = [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
        else:
            out[key] = value
    return out


def make_mock_client(response_content: str) -> RateLimitOpenAIClient:
    """Return a RateLimitOpenAIClient mock whose chat_completion returns response_content."""
    choice = MagicMock()
    choice.message.content = response_content
    completion = MagicMock()
    completion.choices = [choice]
    client = MagicMock(spec=RateLimitOpenAIClient)
    client.chat_completion = AsyncMock(return_value=completion)
    return client