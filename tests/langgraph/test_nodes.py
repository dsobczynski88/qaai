"""
Unit tests for PyJama LangGraph integration.

Tests the PyJamaDataSourceNode, PyJamaRequest validation, and transform functions.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pyjama.langgraph.nodes import (
    PyJamaDataSourceNode,
    PyJamaNodeConfig,
    PyJamaRequest,
)
from pyjama.utils.cache_manager import CacheMode
from pyjama.langgraph.transforms import (
    Requirement,
    TestCase,
    DesignDoc,
    SystemRequirement,
    transform_test_suite_review_to_state,
    transform_test_case_review_to_state,
    transform_hierarchical_trace_to_state,
)


# Fixtures

@pytest.fixture
def pyjama_config():
    """Create a test PyJamaNodeConfig."""
    return PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_client_id",
        client_secret="test_client_secret",
        data_path="./test_data",
        log_path="test_logs",
        max_concurrent=50
    )


@pytest.fixture
def mock_jama_client():
    """Create a mock JamaClient."""
    client = Mock()
    client.get_baselines_versioneditems = Mock(return_value=[])
    client.get_abstract_items = Mock(return_value=[])
    return client


@pytest.fixture
def sample_test_suite_data():
    """Sample data from get_test_suite_reviewer_structure."""
    return [
        {
            "requirement": {
                "req_id": "REQ-001",
                "text": "The system shall process requests"
            },
            "test_cases": [
                {
                    "test_id": "TC-001",
                    "description": "Test request processing",
                    "setup": "Initialize system",
                    "steps": "Step 1. Send request\nStep 2. Verify response",
                    "expectedResults": "ExpectedResult 1. Request processed",
                    "in_review_baseline": True
                }
            ],
            "design_docs": [
                {
                    "doc_id": "DD-001",
                    "name": "Request Processing Design",
                    "description": "Design for request processing module"
                }
            ]
        }
    ]


@pytest.fixture
def sample_test_case_data():
    """Sample data from get_test_case_reviewer_structure."""
    return [
        {
            "test_case": {
                "test_id": "TC-001",
                "description": "Test request processing",
                "setup": "Initialize system",
                "steps": "Step 1. Send request",
                "expectedResults": "ExpectedResult 1. Request processed"
            },
            "requirements": [
                {
                    "req_id": "REQ-001",
                    "text": "The system shall process requests"
                }
            ],
            "design_docs": [
                {
                    "doc_id": "DD-001",
                    "name": "Request Processing Design",
                    "description": "Design for request processing module"
                }
            ]
        }
    ]


@pytest.fixture
def sample_hierarchical_data():
    """Sample data from get_hierarchical_trace_from_gids."""
    return [
        {
            "requirement": {
                "req_id": "GID-001",
                "text": "Software requirement text"
            },
            "system_requirements": [
                {
                    "req_id": "PRQ-001",
                    "text": "System requirement text",
                    "user_needs": [
                        {
                            "req_id": "UND-001",
                            "text": "User need text"
                        }
                    ]
                }
            ]
        }
    ]


# PyJamaRequest Tests

def test_pyjama_request_test_suite_review_valid():
    """Test valid test_suite_review request."""
    request = PyJamaRequest(
        request_type="test_suite_review",
        baseline_id="BASE-123"
    )
    assert request.request_type == "test_suite_review"
    assert request.baseline_id == "BASE-123"


def test_pyjama_request_test_suite_review_missing_baseline():
    """Test test_suite_review request without baseline_id raises error."""
    with pytest.raises(ValueError, match="baseline_id is required"):
        PyJamaRequest(
            request_type="test_suite_review"
        )


def test_pyjama_request_hierarchical_trace_valid():
    """Test valid hierarchical_trace request."""
    request = PyJamaRequest(
        request_type="hierarchical_trace",
        project_name="Test Project",
        identifiers=["GID-001", "GID-002"]
    )
    assert request.request_type == "hierarchical_trace"
    assert request.project_name == "Test Project"
    assert len(request.identifiers) == 2


def test_pyjama_request_hierarchical_trace_missing_project():
    """Test hierarchical_trace request without project_name raises error."""
    with pytest.raises(ValueError, match="project_name is required"):
        PyJamaRequest(
            request_type="hierarchical_trace",
            identifiers=["GID-001"]
        )


def test_pyjama_request_hierarchical_trace_missing_identifiers():
    """Test hierarchical_trace request without identifiers raises error."""
    with pytest.raises(ValueError, match="identifiers is required"):
        PyJamaRequest(
            request_type="hierarchical_trace",
            project_name="Test Project"
        )


@pytest.mark.parametrize("request_type", ["bidirectional_trace", "rtm"])
def test_pyjama_request_identifier_type_valid(request_type):
    """bidirectional_trace / rtm are valid with project_name + identifiers."""
    request = PyJamaRequest(
        request_type=request_type,
        project_name="Test Project",
        identifiers=["GID-001", "PRQ-2"],
    )
    assert request.request_type == request_type
    assert request.project_name == "Test Project"
    assert len(request.identifiers) == 2


@pytest.mark.parametrize("request_type", ["bidirectional_trace", "rtm"])
def test_pyjama_request_identifier_type_missing_project(request_type):
    """bidirectional_trace / rtm without project_name raises error."""
    with pytest.raises(ValueError, match="project_name is required"):
        PyJamaRequest(request_type=request_type, identifiers=["GID-001"])


@pytest.mark.parametrize("request_type", ["bidirectional_trace", "rtm"])
def test_pyjama_request_identifier_type_missing_identifiers(request_type):
    """bidirectional_trace / rtm without identifiers raises error."""
    with pytest.raises(ValueError, match="identifiers is required"):
        PyJamaRequest(request_type=request_type, project_name="Test Project")


# PyJamaNodeConfig Tests

def test_pyjama_node_config_valid():
    """Test valid PyJamaNodeConfig."""
    config = PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_id",
        client_secret="test_secret"
    )
    assert config.host_address == "https://test.jamacloud.com/"
    assert config.max_concurrent == 100  # Default


def test_pyjama_node_config_max_concurrent_validation():
    """Test max_concurrent validation."""
    # Valid range
    config = PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_id",
        client_secret="test_secret",
        max_concurrent=50
    )
    assert config.max_concurrent == 50
    
    # Below minimum
    with pytest.raises(ValueError):
        PyJamaNodeConfig(
            host_address="https://test.jamacloud.com/",
            client_id="test_id",
            client_secret="test_secret",
            max_concurrent=0
        )
    
    # Above maximum
    with pytest.raises(ValueError):
        PyJamaNodeConfig(
            host_address="https://test.jamacloud.com/",
            client_id="test_id",
            client_secret="test_secret",
            max_concurrent=501
        )


def test_pyjama_node_config_cache_mode_default():
    """cache_mode defaults to USE."""
    config = PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_id",
        client_secret="test_secret",
    )
    assert config.cache_mode is CacheMode.USE


def test_pyjama_node_config_cache_mode_explicit():
    """cache_mode accepts a CacheMode enum or its string value."""
    enum_cfg = PyJamaNodeConfig(
        host_address="h", client_id="c", client_secret="s", cache_mode=CacheMode.OFF
    )
    assert enum_cfg.cache_mode is CacheMode.OFF

    str_cfg = PyJamaNodeConfig(
        host_address="h", client_id="c", client_secret="s", cache_mode="refresh"
    )
    assert str_cfg.cache_mode is CacheMode.REFRESH


def test_initialize_clients_propagates_cache_mode(tmp_path):
    """_initialize_clients passes cache_mode through to PyJamaTraceMatrix."""
    config = PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_id",
        client_secret="test_secret",
        data_path=str(tmp_path / "data"),
        log_path=str(tmp_path / "logs"),
        cache_mode=CacheMode.OFF,
    )
    node = PyJamaDataSourceNode(config)

    # Patch JamaClient so no network/auth happens during construction
    with patch("pyjama.langgraph.nodes.JamaClient", return_value=Mock()):
        node._initialize_clients()

    assert node._pyjama_api is not None
    assert node._pyjama_api._cache_mode is CacheMode.OFF


# PyJamaDataSourceNode Tests

def test_pyjama_node_initialization(pyjama_config):
    """Test PyJamaDataSourceNode initialization."""
    node = PyJamaDataSourceNode(pyjama_config)
    assert node.config == pyjama_config
    assert node._jama_client is None  # Lazy initialization
    assert node._pyjama_api is None


@pytest.mark.asyncio
async def test_pyjama_node_missing_request():
    """Test node raises error when state missing pyjama_request."""
    config = PyJamaNodeConfig(
        host_address="https://test.jamacloud.com/",
        client_id="test_id",
        client_secret="test_secret"
    )
    node = PyJamaDataSourceNode(config)
    
    with pytest.raises(ValueError, match="State must contain 'pyjama_request'"):
        await node({})


@pytest.mark.asyncio
async def test_pyjama_node_test_suite_review(pyjama_config, sample_test_suite_data):
    """Test node fetches test_suite_review data."""
    node = PyJamaDataSourceNode(pyjama_config)
    
    # Mock the PyJamaTraceMatrix method
    with patch.object(node, '_initialize_clients'):
        with patch.object(node, '_fetch_test_suite_review', 
                         return_value=sample_test_suite_data) as mock_fetch:
            request = PyJamaRequest(
                request_type="test_suite_review",
                baseline_id="BASE-123"
            )
            
            result = await node({"pyjama_request": request})

            assert "jama_data" in result
            assert "jama_metadata" in result
            assert result["jama_data"] == sample_test_suite_data
            assert result["jama_metadata"]["request_type"] == "test_suite_review"
            assert result["jama_metadata"]["count"] == 1
            mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_pyjama_node_bidirectional_trace(pyjama_config):
    """Node routes bidirectional_trace and returns list data + metadata."""
    node = PyJamaDataSourceNode(pyjama_config)
    sample = [
        {"requirement": {"req_id": "REQ-1", "text": "t"},
         "system_requirements": [], "test_cases": [], "design_docs": []}
    ]

    with patch.object(node, "_initialize_clients"):
        with patch.object(node, "_fetch_bidirectional_trace",
                          return_value=sample) as mock_fetch:
            request = PyJamaRequest(
                request_type="bidirectional_trace",
                project_name="Test Project",
                identifiers=["GID-1"],
            )
            result = await node({"pyjama_request": request})

            assert result["jama_data"] == sample
            assert result["jama_metadata"]["request_type"] == "bidirectional_trace"
            assert result["jama_metadata"]["count"] == 1
            mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_pyjama_node_rtm_dict_count(pyjama_config):
    """Node routes rtm (dict result) and counts total items across categories."""
    node = PyJamaDataSourceNode(pyjama_config)
    sample = {
        "user_needs": [{"req_id": "UN-1"}],
        "system_requirements": [{"req_id": "SR-1"}, {"req_id": "SR-2"}],
        "requirements": [{"req_id": "R-1"}],
        "test_cases": [],
        "design_docs": [],
    }

    with patch.object(node, "_initialize_clients"):
        with patch.object(node, "_fetch_rtm", return_value=sample) as mock_fetch:
            request = PyJamaRequest(
                request_type="rtm",
                project_name="Test Project",
                identifiers=["GID-1"],
            )
            result = await node({"pyjama_request": request})

            assert result["jama_data"] == sample
            assert result["jama_metadata"]["request_type"] == "rtm"
            # total list items: 1 + 2 + 1 + 0 + 0 = 4
            assert result["jama_metadata"]["count"] == 4
            mock_fetch.assert_called_once()


# Transform Tests

def test_transform_test_suite_review(sample_test_suite_data):
    """Test transform_test_suite_review_to_state."""
    states = transform_test_suite_review_to_state(sample_test_suite_data)
    
    assert len(states) == 1
    state = states[0]
    
    # Check requirement
    assert isinstance(state["requirement"], Requirement)
    assert state["requirement"].req_id == "REQ-001"
    assert "process requests" in state["requirement"].text
    
    # Check test cases
    assert len(state["test_cases"]) == 1
    assert isinstance(state["test_cases"][0], TestCase)
    assert state["test_cases"][0].test_id == "TC-001"
    assert state["test_cases"][0].in_review_baseline is True
    
    # Check design docs
    assert len(state["design_docs"]) == 1
    assert isinstance(state["design_docs"][0], DesignDoc)
    assert state["design_docs"][0].doc_id == "DD-001"


def test_transform_test_suite_review_missing_data():
    """Test transform handles missing data gracefully."""
    # Missing req_id
    bad_data = [
        {
            "requirement": {"text": "Some text"},
            "test_cases": [],
            "design_docs": []
        }
    ]
    
    states = transform_test_suite_review_to_state(bad_data)
    assert len(states) == 0  # Should skip invalid entries


def test_transform_test_case_review(sample_test_case_data):
    """Test transform_test_case_review_to_state."""
    states = transform_test_case_review_to_state(sample_test_case_data)
    
    assert len(states) == 1
    state = states[0]
    
    # Check test case
    assert isinstance(state["test_case"], TestCase)
    assert state["test_case"].test_id == "TC-001"
    
    # Check requirements
    assert len(state["requirements"]) == 1
    assert isinstance(state["requirements"][0], Requirement)
    assert state["requirements"][0].req_id == "REQ-001"
    
    # Check design docs
    assert len(state["design_docs"]) == 1
    assert isinstance(state["design_docs"][0], DesignDoc)


def test_transform_hierarchical_trace(sample_hierarchical_data):
    """Test transform_hierarchical_trace_to_state."""
    states = transform_hierarchical_trace_to_state(sample_hierarchical_data)
    
    assert len(states) == 1
    state = states[0]
    
    # Check software requirement
    assert isinstance(state["requirement"], Requirement)
    assert state["requirement"].req_id == "GID-001"
    
    # Check system requirements
    assert len(state["system_requirements"]) == 1
    sys_req = state["system_requirements"][0]
    assert isinstance(sys_req, SystemRequirement)
    assert sys_req.req_id == "PRQ-001"
    
    # Check nested user needs
    assert len(sys_req.user_needs) == 1
    assert isinstance(sys_req.user_needs[0], Requirement)
    assert sys_req.user_needs[0].req_id == "UND-001"


def test_transform_hierarchical_trace_empty_system_reqs():
    """Test transform handles empty system requirements."""
    data = [
        {
            "requirement": {
                "req_id": "GID-001",
                "text": "Software requirement"
            },
            "system_requirements": []
        }
    ]
    
    states = transform_hierarchical_trace_to_state(data)
    
    assert len(states) == 1
    assert len(states[0]["system_requirements"]) == 0


# Pydantic Model Tests

def test_requirement_model():
    """Test Requirement model."""
    req = Requirement(req_id="REQ-001", text="Test requirement")
    assert req.req_id == "REQ-001"
    assert req.text == "Test requirement"


def test_test_case_model():
    """Test TestCase model."""
    tc = TestCase(
        test_id="TC-001",
        description="Test description",
        setup="Test setup",
        steps="Step 1",
        expectedResults="Result 1",
        in_review_baseline=True
    )
    assert tc.test_id == "TC-001"
    assert tc.in_review_baseline is True


def test_test_case_model_defaults():
    """Test TestCase model with defaults."""
    tc = TestCase(test_id="TC-001")
    assert tc.description == ""
    assert tc.setup == ""
    assert tc.steps == ""
    assert tc.expectedResults == ""
    assert tc.in_review_baseline is True


def test_design_doc_model():
    """Test DesignDoc model."""
    dd = DesignDoc(
        doc_id="DD-001",
        name="Design name",
        description="Design description"
    )
    assert dd.doc_id == "DD-001"
    assert dd.name == "Design name"


def test_system_requirement_model():
    """Test SystemRequirement model with nested user needs."""
    user_need = Requirement(req_id="UND-001", text="User need")
    sys_req = SystemRequirement(
        req_id="PRQ-001",
        text="System requirement",
        user_needs=[user_need]
    )
    assert sys_req.req_id == "PRQ-001"
    assert len(sys_req.user_needs) == 1
    assert sys_req.user_needs[0].req_id == "UND-001"


def test_system_requirement_model_empty_user_needs():
    """Test SystemRequirement model with empty user needs."""
    sys_req = SystemRequirement(
        req_id="PRQ-001",
        text="System requirement"
    )
    assert len(sys_req.user_needs) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
