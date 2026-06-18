import os
import json
from pathlib import Path
import pytest
from dotenv import load_dotenv
from pyjama.utils import gen_utils, jama_utils
from pyjama.utils.jama_constants import JAMA_HOST_ADDRESS_ENV
from pyjama.utils.pytest_log_config import init_pytest_logging

load_dotenv()

# Defaults for integration tests (formerly sourced from config.yaml).
DEFAULT_DATA_PATH = "./data"
DEFAULT_LOG_PATH = "./logs"
DEFAULT_MAX_CONCURRENT = 100


def pytest_configure(config):
    """
    Pytest hook called before test collection begins.
    
    Initializes pytest session logging to logs/tests/run-{timestamp}/.
    Respects PYJAMA_TEST_LOG_PATH environment variable for customization.
    Also configures cache mode from --cache CLI option.
    """
    try:
        from pyjama.utils.pytest_log_config import set_pytest_cache_mode
        
        init_pytest_logging()
        
        # Configure cache mode from CLI option
        cache_mode = config.getoption("--cache")
        set_pytest_cache_mode(cache_mode)
        print(f"[PYTEST] Cache mode set to: {cache_mode}")
        
    except Exception as e:
        # Log initialization issues but don't fail pytest startup
        print(f"[WARNING] Failed to initialize pytest logging: {e}")


def pytest_addoption(parser):
    """Add custom CLI options for PyJama tests."""
    parser.addoption(
        "--fixture-file",
        action="store",
        default=None,
        help=(
            "Custom JSONL fixture file (relative to tests/fixtures/) for integration tests. "
            "E.g., 'custom_examples/edge_cases.jsonl'. If not provided, uses default fixtures."
        ),
    )
    parser.addoption(
        "--cache",
        action="store",
        default="use",
        choices=["off", "use", "refresh"],
        help=(
            "Cache mode for PyJama API calls (default: use). "
            "off=disable caching, use=read/write cache, refresh=recompute and write new cache."
        ),
    )


# ============================================================================
# Credential & Host Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def credentials():
    return jama_utils.get_jama_credentials()


@pytest.fixture(scope="session")
def host_address():
    """Jama host address from the JAMA_HOST_ADDRESS env var (skips if unset)."""
    host = os.getenv(JAMA_HOST_ADDRESS_ENV)
    if not host:
        pytest.skip(f"{JAMA_HOST_ADDRESS_ENV} not set; skipping integration test")
    return host


# ============================================================================
# CLI Option Fixtures
# ============================================================================

@pytest.fixture
def fixture_file_option(request) -> str | None:
    """Get --fixture-file CLI option value."""
    return request.config.getoption("--fixture-file")


# ============================================================================
# Fixture File Paths & Loading
# ============================================================================

@pytest.fixture
def test_data_dir() -> Path:
    """Get path to tests/fixtures directory."""
    return Path(__file__).parent / "fixtures"


def get_fixture_file_path(
    fixture_type: str,
    custom_file: str | None = None,
    test_data_dir: Path | None = None,
) -> Path | None:
    """
    Resolve fixture file path based on type and optional custom file.
    
    Args:
        fixture_type: "test_suite_reviewer", "test_case_reviewer", or "bidirectional_trace"
        custom_file: Custom fixture filename from --fixture-file CLI option
        test_data_dir: Path to tests/fixtures directory
        
    Returns:
        Path to fixture file, or None if not found
        
    Raises:
        FileNotFoundError: If custom_file is specified but not found
        ValueError: If fixture_type is unknown
    """
    if test_data_dir is None:
        test_data_dir = Path(__file__).parent / "fixtures"
    
    # If custom file provided, use it (must exist)
    if custom_file:
        custom_path = test_data_dir / custom_file
        if not custom_path.exists():
            raise FileNotFoundError(
                f"Custom fixture file not found: {custom_path}\n"
                f"Looked for: {custom_file} relative to {test_data_dir}"
            )
        return custom_path
    
    # Default file paths by fixture type
    defaults = {
        "test_suite_reviewer": "test_suite_reviewer_inputs.jsonl",
        "test_case_reviewer": "test_case_reviewer_inputs.jsonl",
        "bidirectional_trace": "bidirectional_trace_inputs.jsonl",
    }
    
    if fixture_type not in defaults:
        raise ValueError(f"Unknown fixture_type: {fixture_type}")
    
    default_file = defaults[fixture_type]
    default_path = test_data_dir / default_file
    
    # Return None if default file doesn't exist (allows graceful skip)
    if not default_path.exists():
        return None
    
    return default_path


def load_fixture_file(fixture_path: Path | str) -> list:
    """
    Load fixtures from a JSONL file.
    
    Args:
        fixture_path: Path to JSONL fixture file
        
    Returns:
        List of fixture objects (JSON lines)
    """
    if isinstance(fixture_path, str):
        fixture_path = Path(fixture_path)
    
    if not fixture_path.exists():
        return []
    
    fixtures = []
    with open(fixture_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    fixtures.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"[WARNING] Failed to parse JSON line in {fixture_path}: {e}")
                    continue
    
    return fixtures


# ============================================================================
# Test Fixture Loaders (Parametrization)
# ============================================================================

@pytest.fixture
def test_suite_reviewer_inputs(fixture_file_option, test_data_dir) -> list:
    """Load test suite reviewer input parameters from JSONL file."""
    try:
        fixture_path = get_fixture_file_path(
            "test_suite_reviewer",
            custom_file=fixture_file_option,
            test_data_dir=test_data_dir,
        )
        if fixture_path is None:
            return []
        return load_fixture_file(fixture_path)
    except Exception as e:
        print(f"[ERROR] Failed to load test_suite_reviewer inputs: {e}")
        return []


@pytest.fixture
def test_case_reviewer_inputs(fixture_file_option, test_data_dir) -> list:
    """Load test case reviewer input parameters from JSONL file."""
    try:
        fixture_path = get_fixture_file_path(
            "test_case_reviewer",
            custom_file=fixture_file_option,
            test_data_dir=test_data_dir,
        )
        if fixture_path is None:
            return []
        return load_fixture_file(fixture_path)
    except Exception as e:
        print(f"[ERROR] Failed to load test_case_reviewer inputs: {e}")
        return []


@pytest.fixture
def bidirectional_trace_inputs(fixture_file_option, test_data_dir) -> list:
    """Load bidirectional trace input parameters from JSONL file."""
    try:
        fixture_path = get_fixture_file_path(
            "bidirectional_trace",
            custom_file=fixture_file_option,
            test_data_dir=test_data_dir,
        )
        if fixture_path is None:
            return []
        return load_fixture_file(fixture_path)
    except Exception as e:
        print(f"[ERROR] Failed to load bidirectional_trace inputs: {e}")
        return []


# ============================================================================
# PyJama Instance Fixture
# ============================================================================

@pytest.fixture
def pyjama_instance(host_address, credentials):
    """Create a PyJamaTraceMatrix instance for integration tests.
    
    This fixture automatically uses the pytest session logger to ensure all logs
    go to the same pyjama.log file (no duplicate PyJamaMatrix.log files).
    Also applies cache mode and file name settings from pytest CLI options.
    """
    from py_jama_rest_client.client import JamaClient
    from pyjama.jama import PyJamaTraceMatrix
    from pyjama.utils.pytest_log_config import (
        get_pytest_logger,
        get_pytest_log_dir,
        get_pytest_cache_mode,
        get_pytest_input_file_name,
        get_pytest_output_file_name,
    )

    jama_client = JamaClient(host_address, credentials, oauth=True, verify=True)
    
    # Get pytest session logger and log directory
    pytest_logger = get_pytest_logger()
    pytest_log_dir = str(get_pytest_log_dir())
    
    # Get cache and file settings from pytest config
    cache_mode = get_pytest_cache_mode() or "use"
    inputs_file_name = get_pytest_input_file_name()
    outputs_file_name = get_pytest_output_file_name()
    
    # Determine enable_cache flag: False if cache_mode is "off", True otherwise
    enable_cache = cache_mode.lower() != "off"

    return PyJamaTraceMatrix(
        jama_client,
        data_path=DEFAULT_DATA_PATH,
        log_path=DEFAULT_LOG_PATH,
        max_concurrent=DEFAULT_MAX_CONCURRENT,
        log_dir=pytest_log_dir,
        logger=pytest_logger,
        cache_mode=cache_mode,
        enable_cache=enable_cache,
        inputs_file_name=inputs_file_name,
        outputs_file_name=outputs_file_name,
    )


# ============================================================================
# JSON Recorders (for capturing real API responses)
# ============================================================================

@pytest.fixture(scope="session")
def jsonl_recorders():
    """
    Session-scoped fixture that clears input/output JSONL files once at session
    start, yields (record_input, record_output) append functions, then optionally
    generates an HTML viewer at session teardown if output file has records.
    
    Uses the pytest session log directory and customizable file names from CLI options.
    """
    from pyjama.utils.pytest_log_config import (
        get_pytest_log_dir,
        get_pytest_input_file_name,
        get_pytest_output_file_name,
    )
    
    try:
        # Use pytest session log directory (shared with pyjama.log)
        run_dir = Path(str(get_pytest_log_dir()))
    except RuntimeError:
        # Fallback if pytest logging not initialized
        log_path = DEFAULT_LOG_PATH
        run_dir = Path(log_path)
        if not run_dir.exists():
            run_dir = Path(gen_utils.make_output_directory(log_path))
        else:
            run_dirs = sorted(run_dir.glob("run-*"))
            if run_dirs:
                run_dir = run_dirs[-1]
            else:
                run_dir = Path(gen_utils.make_output_directory(log_path))
    
    # Get customizable file names from pytest config (or use defaults)
    inputs_file_name = get_pytest_input_file_name()
    outputs_file_name = get_pytest_output_file_name()
    
    inputs_path = run_dir / inputs_file_name
    outputs_path = run_dir / outputs_file_name
    
    # Clear files at session start
    inputs_path.write_text("", encoding="utf-8")
    outputs_path.write_text("", encoding="utf-8")
    
    def record_input(data: dict) -> None:
        """Append input data to input file"""
        with inputs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, default=str) + "\n")
    
    def record_output(data: dict) -> None:
        """Append output data to output file"""
        with outputs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, default=str) + "\n")
    
    yield record_input, record_output
    
    # Optional: Generate summary at teardown
    try:
        # Check if output file has content
        if outputs_path.stat().st_size > 0:
            print(f"\n[JSONL] Recorded inputs: {inputs_path}")
            print(f"[JSONL] Recorded outputs: {outputs_path}")
            
            # Optional: Generate HTML viewer if you have a viewer module
            # from pyjama.viewer import write_viewer
            # viewer_path = write_viewer(outputs_path)
            # if viewer_path:
            #     print(f"[JSONL] Generated viewer: {viewer_path}")
    except Exception as exc:
        print(f"\n[JSONL] Teardown warning: {exc}")


# ============================================================================
# Dynamic Test Parametrization
# ============================================================================

def pytest_generate_tests(metafunc):
    """
    Dynamically generate test parameters from fixture files.
    
    This hook allows tests to run against all fixtures in JSONL files
    instead of just the first one. Respects --fixture-file CLI option.
    """
    custom_fixture_file = metafunc.config.getoption("--fixture-file")
    test_data_dir = Path(metafunc.config.rootdir) / "tests" / "fixtures"
    
    # For test_get_test_suite_reviewer_structure (uses test_suite_input param)
    if "test_suite_input" in metafunc.fixturenames:
        try:
            fixture_path = get_fixture_file_path(
                "test_suite_reviewer",
                custom_file=custom_fixture_file,
                test_data_dir=test_data_dir,
            )
            if fixture_path is None:
                metafunc.parametrize(
                    "test_suite_input",
                    [pytest.param(None, marks=pytest.mark.skip(reason="No test_suite_reviewer inputs found"))]
                )
            else:
                fixtures = load_fixture_file(fixture_path)
                if fixtures:
                    # Create test IDs from test_name field for better test output
                    ids = [f.get("test_name", f"fixture_{i}") for i, f in enumerate(fixtures)]
                    metafunc.parametrize("test_suite_input", fixtures, ids=ids)
                else:
                    metafunc.parametrize(
                        "test_suite_input",
                        [pytest.param(None, marks=pytest.mark.skip(reason="No fixtures in inputs file"))]
                    )
        except Exception as e:
            print(f"[ERROR] Failed to parametrize test_suite_input: {e}")
            metafunc.parametrize(
                "test_suite_input",
                [pytest.param(None, marks=pytest.mark.skip(reason=str(e)))]
            )
    
    # For test_get_test_case_reviewer_structure (uses test_case_input param)
    if "test_case_input" in metafunc.fixturenames:
        try:
            fixture_path = get_fixture_file_path(
                "test_case_reviewer",
                custom_file=custom_fixture_file,
                test_data_dir=test_data_dir,
            )
            if fixture_path is None:
                metafunc.parametrize(
                    "test_case_input",
                    [pytest.param(None, marks=pytest.mark.skip(reason="No test_case_reviewer inputs found"))]
                )
            else:
                fixtures = load_fixture_file(fixture_path)
                if fixtures:
                    ids = [f.get("test_name", f"fixture_{i}") for i, f in enumerate(fixtures)]
                    metafunc.parametrize("test_case_input", fixtures, ids=ids)
                else:
                    metafunc.parametrize(
                        "test_case_input",
                        [pytest.param(None, marks=pytest.mark.skip(reason="No fixtures in inputs file"))]
                    )
        except Exception as e:
            print(f"[ERROR] Failed to parametrize test_case_input: {e}")
            metafunc.parametrize(
                "test_case_input",
                [pytest.param(None, marks=pytest.mark.skip(reason=str(e)))]
            )
    
    # For test_get_bidirectional_trace_from_gids (uses bidirectional_input param)
    if "bidirectional_input" in metafunc.fixturenames:
        try:
            fixture_path = get_fixture_file_path(
                "bidirectional_trace",
                custom_file=custom_fixture_file,
                test_data_dir=test_data_dir,
            )
            if fixture_path is None:
                metafunc.parametrize(
                    "bidirectional_input",
                    [pytest.param(None, marks=pytest.mark.skip(reason="No bidirectional_trace inputs found"))]
                )
            else:
                fixtures = load_fixture_file(fixture_path)
                if fixtures:
                    ids = [f.get("test_name", f"fixture_{i}") for i, f in enumerate(fixtures)]
                    metafunc.parametrize("bidirectional_input", fixtures, ids=ids)
                else:
                    metafunc.parametrize(
                        "bidirectional_input",
                        [pytest.param(None, marks=pytest.mark.skip(reason="No fixtures in inputs file"))]
                    )
        except Exception as e:
            print(f"[ERROR] Failed to parametrize bidirectional_input: {e}")
            metafunc.parametrize(
                "bidirectional_input",
                [pytest.param(None, marks=pytest.mark.skip(reason=str(e)))]
            )
    

