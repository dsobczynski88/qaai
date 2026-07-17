"""
LangGraph-compatible nodes for fetching Jama data in real-time.

This module provides reusable nodes that can be imported by external
LangGraph applications to replace static JSONL file inputs with live
Jama API calls.
"""

from typing import Literal, Optional, List, Any, Dict, Union
from pydantic import BaseModel, Field, model_validator
from py_jama_rest_client.client import JamaClient
from ..jama.pyjama import PyJamaTraceMatrix
from ..utils.cache_manager import CacheMode
from ..utils.proj_log import ProjectLogger
from ..utils.gen_utils import make_output_directory
from ..utils.jama_constants import PYJAMA_LOGGERNAME
import asyncio
from functools import partial
import logging
import os


# Module-level fallback logger. Used only if something logs before a
# PyJamaDataSourceNode is constructed; per-node logging goes through the
# ProjectLogger configured in __init__ (self._logger).
logger = logging.getLogger(__name__)


class PyJamaRequest(BaseModel):
    """
    Request configuration for PyJama data source node.

    Attributes:
        request_type: Type of Jama data to fetch
        project_name: Jama project name (required for hierarchical_trace,
            bidirectional_trace, rtm)
        baseline_id: Baseline ID (required for test_suite_review, test_case_review)
        identifiers: List of GIDs or document keys (required for hierarchical_trace,
            bidirectional_trace, rtm)
        api_id_key: Optional API ID key override
        design_typekey: Optional design document type key(s) override (str or list)
        testcase_typekey: Optional test case type key(s) override (str or list)
        requirement_typekeys: Optional requirement type key(s) override (str or list)
        user_need_typekey: Optional user need type key(s) override (str or list)
        prq_type_field: Optional PRQ type field name override
    """

    request_type: Literal[
        "test_suite_review",
        "test_case_review",
        "requirement_review",
        "hierarchical_trace",
        "bidirectional_trace",
        "rtm",
    ] = Field(..., description="Type of Jama data to fetch")

    project_name: Optional[str] = Field(
        None,
        description="Jama project name (required for identifier-based request types)"
    )

    baseline_id: Optional[str] = Field(
        None,
        description="Baseline ID (required for test_suite_review, test_case_review)"
    )

    identifiers: Optional[List[str]] = Field(
        None,
        description="List of GIDs or document keys (required for identifier-based request types)"
    )
    
    # Optional parameters for fine-tuning
    api_id_key: Optional[str] = Field(None, description="API ID key override")
    design_typekey: Optional[Union[str, List[str]]] = Field(
        None, description="Design document type key(s): a single typekey or a list of typekeys"
    )
    testcase_typekey: Optional[Union[str, List[str]]] = Field(
        None, description="Test case type key(s): a single typekey or a list of typekeys"
    )
    requirement_typekeys: Optional[Union[str, List[str]]] = Field(
        None, description="Requirement type key(s): a single typekey or a list of typekeys"
    )
    user_need_typekey: Optional[Union[str, List[str]]] = Field(
        None, description="User need type key(s): a single typekey or a list of typekeys"
    )
    prq_type_field: Optional[str] = Field(None, description="PRQ type field name")
    
    # Request types keyed by a baseline vs. a project + identifier list
    _BASELINE_TYPES = ("test_suite_review", "test_case_review", "requirement_review")
    _IDENTIFIER_TYPES = ("hierarchical_trace", "bidirectional_trace", "rtm")

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "PyJamaRequest":
        """Enforce the fields each request type needs.

        A model_validator (not field_validators) is used so the checks run even
        when the conditionally-required field is absent — Pydantic v2 skips
        field_validators on unset default fields.
        """
        rt = self.request_type
        if rt in self._BASELINE_TYPES and not self.baseline_id:
            raise ValueError(f"baseline_id is required for request_type='{rt}'")
        if rt in self._IDENTIFIER_TYPES:
            if not self.project_name:
                raise ValueError(f"project_name is required for request_type='{rt}'")
            if not self.identifiers:
                raise ValueError(f"identifiers is required for request_type='{rt}'")
        return self


class PyJamaNodeConfig(BaseModel):
    """
    Configuration for initializing PyJamaTraceMatrix.
    
    Attributes:
        host_address: Jama Connect host URL (e.g., "https://your-org.jamacloud.com/")
        client_id: OAuth client ID
        client_secret: OAuth client secret
        data_path: Path for data output (default: "./data")
        log_path: Base directory for log files (default: "logs"). A timestamped
            run-<timestamp>/ subdirectory is created inside it for each node, and
            all logs (node, PyJamaTraceMatrix, assemblers, cache) are written to a
            single pyjama.log there via ProjectLogger.
        max_concurrent: Maximum concurrent API requests (default: 100, range: 1-500)
        oauth: Use OAuth authentication (default: True)
        cache_mode: Tier-3 disk cache behavior (default: USE). One of
            CacheMode.OFF / USE / REFRESH (the string "off"/"use"/"refresh" also works).
            Controls whether routed trace methods read from ./cache/source/ first.
        test_mode: When True, run strictly from the disk cache (default: False).
            No JamaClient is constructed and the Jama API is never contacted, so
            mock/absent credentials are accepted; requests are served from
            ./cache/source/ keyed by baseline_id / identifiers and a cache miss
            raises CacheMissError.
    """

    host_address: Optional[str] = Field(None, description="Jama Connect host URL")
    client_id: Optional[str] = Field(None, description="OAuth client ID")
    client_secret: Optional[str] = Field(None, description="OAuth client secret")
    data_path: str = Field(default="./data", description="Path for data output")
    log_path: str = Field(default="logs", description="Path for log files")
    log_file_name: str = Field(default="pyjama.log", description="Log file name")
    max_concurrent: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum concurrent API requests"
    )
    oauth: bool = Field(default=True, description="Use OAuth authentication")
    cache_mode: CacheMode = Field(
        default=CacheMode.USE,
        description="Tier-3 disk cache mode (off/use/refresh)"
    )
    test_mode: bool = Field(
        default=False,
        description="Run strictly from disk cache (no JamaClient, no API calls)"
    )

    @model_validator(mode="after")
    def _validate_credentials(self) -> "PyJamaNodeConfig":
        """Require Jama credentials unless running in test_mode."""
        if not self.test_mode:
            missing = [
                name for name, value in (
                    ("host_address", self.host_address),
                    ("client_id", self.client_id),
                    ("client_secret", self.client_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Missing required Jama credentials "
                    f"({', '.join(missing)}); set them or use test_mode=True."
                )
        return self


class PyJamaDataSourceNode:
    """
    LangGraph-compatible node that fetches data from Jama Connect in real-time.
    
    This node replaces static JSONL file inputs with live API calls to Jama,
    enabling dynamic trace matrix generation within LangGraph workflows.
    
    The node is designed to be used at the beginning of a LangGraph workflow,
    fetching data based on a request configuration and returning it in a format
    compatible with downstream LangGraph nodes.
    
    Usage:
        # In your LangGraph application
        from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig
        
        config = PyJamaNodeConfig(
            host_address="https://your-org.jamacloud.com/",
            client_id=os.getenv("JAMA_CLIENT_ID"),
            client_secret=os.getenv("JAMA_CLIENT_SECRET")
        )
        
        jama_node = PyJamaDataSourceNode(config)
        
        # Add to your StateGraph
        sg.add_node("jama_source", jama_node)
        sg.add_edge(START, "jama_source")
        
    State Requirements:
        Input state must contain:
            - pyjama_request: PyJamaRequest configuration
        
        Output state will contain:
            - jama_data: List[Dict] containing fetched Jama data
            - jama_metadata: Dict with fetch metadata (count, request_type, etc.)
    
    Example:
        >>> config = PyJamaNodeConfig(
        ...     host_address="https://your-org.jamacloud.com/",
        ...     client_id="your_client_id",
        ...     client_secret="your_client_secret"
        ... )
        >>> node = PyJamaDataSourceNode(config)
        >>> 
        >>> # Create request
        >>> request = PyJamaRequest(
        ...     request_type="test_suite_review",
        ...     baseline_id="BASE-84398"
        ... )
        >>> 
        >>> # Execute node
        >>> result = await node({"pyjama_request": request})
        >>> print(f"Fetched {len(result['jama_data'])} requirements")
    """
    
    def __init__(self, config: PyJamaNodeConfig):
        """
        Initialize the node with Jama client configuration.
        
        Args:
            config: PyJamaNodeConfig with Jama connection details
        """
        self.config = config
        self._jama_client = None
        self._pyjama_api = None

        # Resolve a single timestamped run directory for this node and configure
        # one shared ProjectLogger. PyJamaTraceMatrix, its assemblers, and the
        # cache all log into the same file (see _initialize_clients).
        self._log_dir = make_output_directory(config.log_path)
        self._logger = ProjectLogger(
            name=PYJAMA_LOGGERNAME,
            log_file=os.path.join(self._log_dir, self.config.log_file_name),
        ).config().get_logger()

        self._logger.info("Initialized PyJamaDataSourceNode with config: %s", {
            "host_address": config.host_address,
            "data_path": config.data_path,
            "log_path": config.log_path,
            "log_dir": self._log_dir,
            "max_concurrent": config.max_concurrent,
            "cache_mode": config.cache_mode.value,
        })
    
    def _initialize_clients(self):
        """Lazy initialization of Jama clients."""
        if self._pyjama_api is not None:
            return

        if self.config.test_mode:
            # Cache-only: do NOT build a JamaClient. JamaClient(oauth=True)
            # fetches an OAuth token in its constructor, which fails with mock
            # credentials — and the API is never used in test_mode anyway.
            self._logger.info("Initializing PyJamaTraceMatrix in test_mode (cache-only, no JamaClient)")
            self._jama_client = None
            self._pyjama_api = PyJamaTraceMatrix(
                jama_client=None,
                data_path=self.config.data_path,
                max_concurrent=self.config.max_concurrent,
                cache_mode=self.config.cache_mode,
                test_mode=True,
                log_dir=self._log_dir,
                logger=self._logger,
            )
            self._logger.info("PyJamaTraceMatrix initialized in test_mode")
            return

        self._logger.info("Initializing Jama clients...")

        self._jama_client = JamaClient(
            host_domain=self.config.host_address,
            credentials=(self.config.client_id, self.config.client_secret),
            oauth=self.config.oauth
        )

        self._pyjama_api = PyJamaTraceMatrix(
            jama_client=self._jama_client,
            data_path=self.config.data_path,
            max_concurrent=self.config.max_concurrent,
            cache_mode=self.config.cache_mode,
            log_dir=self._log_dir,
            logger=self._logger,
        )

        self._logger.info("Jama clients initialized successfully")
    
    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the node: fetch data from Jama based on request configuration.
        
        This method is called by LangGraph when the node is executed in the graph.
        It extracts the PyJamaRequest from the state, routes to the appropriate
        PyJamaTraceMatrix method, and returns the fetched data.
        
        Args:
            state: LangGraph state dict containing 'pyjama_request' key
            
        Returns:
            Updated state dict with:
                - jama_data: List[Dict] containing fetched data
                - jama_metadata: Dict with fetch metadata
        
        Raises:
            ValueError: If state is missing required keys or request validation fails
            Exception: If Jama API calls fail
        """
        request = state.get("pyjama_request")
        
        if request is None:
            raise ValueError(
                "State must contain 'pyjama_request' key with PyJamaRequest configuration"
            )
        
        # Convert dict to PyJamaRequest if needed
        if isinstance(request, dict):
            request = PyJamaRequest(**request)
        
        self._logger.info("Processing PyJama request: type=%s", request.request_type)
        
        # Initialize clients if needed (lazy initialization)
        self._initialize_clients()
        
        # Route to appropriate method based on request_type
        if request.request_type == "test_suite_review":
            data = await self._fetch_test_suite_review(request)
        elif request.request_type == "test_case_review":
            data = await self._fetch_test_case_review(request)
        elif request.request_type == "requirement_review":
            data = await self._fetch_requirement_review(request)
        elif request.request_type == "hierarchical_trace":
            data = await self._fetch_hierarchical_trace(request)
        elif request.request_type == "bidirectional_trace":
            data = await self._fetch_bidirectional_trace(request)
        elif request.request_type == "rtm":
            data = await self._fetch_rtm(request)
        else:
            raise ValueError(f"Unknown request_type: {request.request_type}")

        # rtm returns a dict (category -> list); the others return a list.
        count = len(data) if isinstance(data, list) else sum(
            len(v) for v in data.values() if isinstance(v, list)
        )

        # Build metadata
        metadata = {
            "request_type": request.request_type,
            "count": count,
            "baseline_id": request.baseline_id,
            "project_name": request.project_name,
            "identifiers": request.identifiers,
        }

        self._logger.info(
            "Successfully fetched %d items for request_type=%s",
            count,
            request.request_type
        )

        return {
            "jama_data": data,
            "jama_metadata": metadata
        }
    
    async def _fetch_test_suite_review(self, request: PyJamaRequest) -> List[Dict]:
        """
        Fetch test suite reviewer structure from Jama.
        
        Calls PyJamaTraceMatrix.get_test_suite_reviewer_structure() in an executor
        to avoid blocking the async event loop.
        
        Args:
            request: PyJamaRequest with baseline_id
            
        Returns:
            List of requirement dictionaries with test cases and design docs
        """
        if not request.baseline_id:
            raise ValueError("baseline_id is required for test_suite_review")
        
        self._logger.info(
            "Fetching test suite review for baseline_id=%s",
            request.baseline_id
        )
        
        # PyJamaTraceMatrix methods are synchronous, so run in executor
        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_test_suite_reviewer_structure,
            baseline_id=request.baseline_id,
            api_id_key=request.api_id_key,
            design_typekey=request.design_typekey,
            testcase_typekey=request.testcase_typekey
        )
        
        result = await loop.run_in_executor(None, func)

        self._logger.info(
            "Fetched %d requirements for baseline_id=%s",
            len(result),
            request.baseline_id
        )

        return result

    async def _fetch_requirement_review(self, request: PyJamaRequest) -> List[Dict]:
        """
        Fetch requirement reviewer structure from Jama.

        For a requirement-review baseline whose items are requirement ids directly.
        Calls PyJamaTraceMatrix.get_requirement_reviewer_structure() in an executor
        to avoid blocking the async event loop. Returns the same per-requirement
        structure as test_suite_review.

        Args:
            request: PyJamaRequest with baseline_id

        Returns:
            List of requirement dictionaries with test cases and design docs
        """
        if not request.baseline_id:
            raise ValueError("baseline_id is required for requirement_review")

        self._logger.info(
            "Fetching requirement review for baseline_id=%s",
            request.baseline_id
        )

        # PyJamaTraceMatrix methods are synchronous, so run in executor
        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_requirement_reviewer_structure,
            baseline_id=request.baseline_id,
            api_id_key=request.api_id_key,
            design_typekey=request.design_typekey,
            testcase_typekey=request.testcase_typekey
        )

        result = await loop.run_in_executor(None, func)

        self._logger.info(
            "Fetched %d requirements for baseline_id=%s",
            len(result),
            request.baseline_id
        )

        return result

    async def _fetch_test_case_review(self, request: PyJamaRequest) -> List[Dict]:
        """
        Fetch test case reviewer structure from Jama.
        
        Calls PyJamaTraceMatrix.get_test_case_reviewer_structure() in an executor
        to avoid blocking the async event loop.
        
        Args:
            request: PyJamaRequest with baseline_id
            
        Returns:
            List of test case dictionaries with requirements and design docs
        """
        if not request.baseline_id:
            raise ValueError("baseline_id is required for test_case_review")
        
        self._logger.info(
            "Fetching test case review for baseline_id=%s",
            request.baseline_id
        )
        
        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_test_case_reviewer_structure,
            baseline_id=request.baseline_id,
            api_id_key=request.api_id_key,
            design_typekey=request.design_typekey,
            requirement_typekeys=request.requirement_typekeys
        )
        
        result = await loop.run_in_executor(None, func)
        
        self._logger.info(
            "Fetched %d test cases for baseline_id=%s",
            len(result),
            request.baseline_id
        )
        
        return result
    
    async def _fetch_hierarchical_trace(self, request: PyJamaRequest) -> List[Dict]:
        """
        Fetch hierarchical trace from Jama.
        
        Calls PyJamaTraceMatrix.get_hierarchical_trace_from_gids() in an executor
        to avoid blocking the async event loop.
        
        Args:
            request: PyJamaRequest with project_name and identifiers
            
        Returns:
            List of software requirement dictionaries with hierarchical traces
        """
        if not request.project_name or not request.identifiers:
            raise ValueError(
                "project_name and identifiers are required for hierarchical_trace"
            )
        
        self._logger.info(
            "Fetching hierarchical trace for project=%s, identifiers=%d",
            request.project_name,
            len(request.identifiers)
        )
        
        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_hierarchical_trace_from_gids,
            identifiers=request.identifiers,
            project_name=request.project_name,
            api_id_key=request.api_id_key,
            user_need_typekey=request.user_need_typekey,
            prq_type_field=request.prq_type_field
        )
        
        result = await loop.run_in_executor(None, func)

        self._logger.info(
            "Fetched %d software requirements for project=%s",
            len(result),
            request.project_name
        )

        return result

    async def _fetch_bidirectional_trace(self, request: PyJamaRequest) -> List[Dict]:
        """
        Fetch bidirectional trace (upstream + downstream) from Jama.

        Calls PyJamaTraceMatrix.get_bidirectional_trace_from_gids() in an executor
        to avoid blocking the async event loop.

        Args:
            request: PyJamaRequest with project_name and identifiers

        Returns:
            List of requirement dicts, each with nested system_requirements
            (with user_needs), test_cases, and design_docs
        """
        if not request.project_name or not request.identifiers:
            raise ValueError(
                "project_name and identifiers are required for bidirectional_trace"
            )

        self._logger.info(
            "Fetching bidirectional trace for project=%s, identifiers=%d",
            request.project_name,
            len(request.identifiers)
        )

        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_bidirectional_trace_from_gids,
            identifiers=request.identifiers,
            project_name=request.project_name,
            api_id_key=request.api_id_key,
            user_need_typekey=request.user_need_typekey,
            design_typekey=request.design_typekey,
            testcase_typekey=request.testcase_typekey,
            prq_type_field=request.prq_type_field,
        )

        result = await loop.run_in_executor(None, func)

        self._logger.info(
            "Fetched %d software requirements (bidirectional) for project=%s",
            len(result),
            request.project_name
        )

        return result

    async def _fetch_rtm(self, request: PyJamaRequest) -> Dict[str, List[Dict]]:
        """
        Fetch a flat RTM (Requirements Traceability Matrix) from Jama.

        Calls PyJamaTraceMatrix.get_rtm_from_gids() in an executor to avoid
        blocking the async event loop.

        Args:
            request: PyJamaRequest with project_name and identifiers

        Returns:
            Dict with keys user_needs, system_requirements, requirements,
            test_cases, design_docs — each a list
        """
        if not request.project_name or not request.identifiers:
            raise ValueError(
                "project_name and identifiers are required for rtm"
            )

        self._logger.info(
            "Fetching RTM for project=%s, identifiers=%d",
            request.project_name,
            len(request.identifiers)
        )

        loop = asyncio.get_event_loop()
        func = partial(
            self._pyjama_api.get_rtm_from_gids,
            identifiers=request.identifiers,
            project_name=request.project_name,
            api_id_key=request.api_id_key,
            design_typekey=request.design_typekey,
            testcase_typekey=request.testcase_typekey,
            user_need_typekey=request.user_need_typekey,
            prq_type_field=request.prq_type_field,
        )

        result = await loop.run_in_executor(None, func)

        self._logger.info(
            "Fetched RTM for project=%s (%d categories)",
            request.project_name,
            len(result)
        )

        return result
