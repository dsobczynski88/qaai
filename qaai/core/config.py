import os
import logging
from pathlib import Path
from typing import Optional, Union
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from qaai.core.constants import (
    DEFAULT_MAX_REQUESTS_PER_MINUTE,
    DEFAULT_MAX_TOKENS_PER_MINUTE,
    DEFAULT_MAX_OUTPUT_TOKENS,
    TOKEN_USAGE_JSONL_FILENAME,
)

logger = logging.getLogger(__name__)


class PromptConfig(BaseModel):
    """Jinja2 template paths used by each LLM node across reviewer graphs.
    
    Paths can be either:
    - Legacy flat filenames (for backward compatibility): "decomposer-v4.jinja2"
    - New versioned paths: "decomposer/v5.0.0/template.jinja2"
    
    Use PromptConfig.from_set("set_name") to load from a named prompt set manifest.
    """
    # Name of the prompt set this config was resolved from (set by from_set()).
    # Threaded into the cache key so results never alias across sets that pin the
    # same node version (e.g. test_suite_reviewer_v3 and _v4 both use coverage v8).
    set_name: Optional[str] = None

    # Test Suite Reviewer prompts
    decomposer: str = "decomposer/v6.0.0/template.jinja2"
    summarizer: str = "summarizer/v4.0.0/template.jinja2"
    design_summarizer: str = "design_summarizer/v1.0.0/template.jinja2"
    coverage: str = "coverage_evaluator/v8.0.0/template.jinja2"
    synthesizer: str = "synthesizer/v8.0.0/template.jinja2"
    
    # Test Case Reviewer prompts
    single_test_aggregator: str = "single_test_aggregator/v6.0.0/template.jinja2"
    single_test_coverage_eval: str = "single_test_coverage_eval/v3.0.0/template.jinja2"
    single_test_logical_steps: str = "single_test_logical_steps/v3.0.0/template.jinja2"
    single_test_prereqs: str = "single_test_prereqs/v3.0.0/template.jinja2"
    
    # Hazard reviewer prompts (H1-H7 + final assessor)
    hazard_h1: str = "hazard_h1/v3.0.0/template.jinja2"
    hazard_h2: str = "hazard_h2/v1.0.0/template.jinja2"
    hazard_h3: str = "hazard_h3/v4.0.0/template.jinja2"
    hazard_h4: str = "hazard_h4/v1.0.0/template.jinja2"
    hazard_h5: str = "hazard_h5/v1.0.0/template.jinja2"
    hazard_h6: str = "hazard_h6/v2.0.0/template.jinja2"
    hazard_h7: str = "hazard_h7/v1.0.0/template.jinja2"
    hazard_final: str = "hazard_final_assessor/v1.0.0/template.jinja2"
    hazard_design_summarizer: str = "hazard_design_summarizer/v1.0.0/template.jinja2"
    hazard_needs_summarizer: str = "hazard_needs_summarizer/v1.0.0/template.jinja2"
    
    @classmethod
    def from_set(cls, set_name: str) -> "PromptConfig":
        """Load prompt config from a named set manifest.
        
        Args:
            set_name: Name of the prompt set (e.g., "test_case_reviewer_v2")
            
        Returns:
            PromptConfig with paths resolved from the set manifest
            
        Example:
            >>> config = PromptConfig.from_set("test_case_reviewer_v2")
            >>> config.single_test_aggregator
            'single_test_aggregator/v6.0.0/template.jinja2'
        """
        from qaai.prompts._registry import PROMPTS_DIR, load_set
        resolved = load_set(set_name)

        # Start with current defaults
        current = cls()
        kwargs = current.model_dump()

        # Override with resolved prompts from the set. The template directory can differ
        # from the logical role key (e.g. role "coverage" -> dir "coverage_evaluator"), so
        # derive the relative path from the resolved template path rather than the role.
        for role, prompt in resolved.prompts.items():
            kwargs[role] = prompt.template_path.relative_to(PROMPTS_DIR).as_posix()

        kwargs["set_name"] = set_name
        return cls(**kwargs)

class Settings(BaseSettings):
    """Application settings loaded from environment variables.
    
    Environment Variables:
        API_KEY: API key for the LLM service (required)
        API_BASE_URL: Base URL for the API endpoint (required)
        API_MODEL: Model identifier (required)
        MAX_REQUESTS_PER_MINUTE: Rate limit for API requests (default: 490)
        MAX_TOKENS_PER_MINUTE: Token rate limit (default: 200000)
        MAX_OUTPUT_TOKENS: Maximum output tokens per request (default: 16000)
        PROMPT_SET: Named prompt set to load (optional, e.g., "test_case_reviewer_v2")
    """
    # Deployment environment selector (DEV / TEST / PROD). DEV (default) reads from
    # the local .env exactly as before. TEST/PROD hydrate the process environment
    # from the AWS secret store (or a prefixed-.env local mimic) via
    # EnvVariableRetriever in __init__ — see below.
    app_env: str = Field(default="DEV", alias='APP_ENV')

    openai_api_key: str = Field(..., alias='API_KEY')
    url: Union[str, None] = Field(default=None, alias='API_BASE_URL')
    model: str = Field(..., alias='API_MODEL')
    model_kwargs: dict = {}
    max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE
    max_tokens_per_minute: int = DEFAULT_MAX_TOKENS_PER_MINUTE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    # Default points at ./logs/qaai.log but creates no directory at import time.
    # The real per-run directory is set by logging_config.start_new_run() — once at
    # startup (create_app) and again at the start of every review request — which
    # updates this value to logs/run-<ts>/qaai.log.
    log_file_path: str = "./logs/qaai.log"
    log_file_name: str = "qaai.log"
    # Base directory under which start_new_run() creates each run-<ts>/ folder.
    # Production/front-end uses ./logs; the test harness (conftest) overrides this
    # to ./logs/tests so test artifacts never mix with server runs.
    log_base_dir: str = "./logs"

    # Token cost rates in USD per million tokens — set in .env to match your model pricing.
    token_cost_input_per_m: float = Field(default=1.00, alias="TOKEN_COST_INPUT_PER_M")
    token_cost_output_per_m: float = Field(default=5.00, alias="TOKEN_COST_OUTPUT_PER_M")

    # Reviewer cache, shared by all three reviewers (set ENABLE_CACHE=false to
    # disable entirely; CACHE_DIR holds one folder per entity id — e.g.
    # shared/runs/HAZ-PUMP-001, shared/runs/REQ-PUMP-101, shared/runs/TEST-PUMP-201).
    # Files are immutable + timestamped ({node}_{version}_{ts}.json); reads select
    # the newest. The default dir is ./shared/runs, a sibling of the pyjama JAMA
    # source cache at ./shared/source (which is owned by the pyjama package and is
    # NOT derived from this setting — it is unaffected).
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")
    cache_dir: str = Field(default="./shared/runs", alias="CACHE_DIR")
    enable_cache: bool = Field(default=True, alias="ENABLE_CACHE")

    # Optional JAMA / Pyjama integration settings
    jama_host_address: Optional[str] = Field(default=None, alias='JAMA_HOST_ADDRESS')
    jama_client_id: Optional[str] = Field(default=None, alias='JAMA_CLIENT_ID')
    jama_client_secret: Optional[str] = Field(default=None, alias='JAMA_CLIENT_SECRET')

    # When True, the PyJama data source runs strictly from the disk cache: no
    # live JAMA API calls are made and invalid/mock credentials are tolerated.
    # Acts as the server-wide default; the API "test mode" toggle overrides it
    # per request. See PyJamaNodeConfig.test_mode in the pyjama package.
    pyjama_test_mode: bool = Field(default=False, alias='PYJAMA_TEST_MODE')

    # Optional prompt set name - if specified, overrides default prompt_config
    prompt_set: Optional[str] = Field(default=None, alias='PROMPT_SET')
    
    _prompt_config_cache: Optional[PromptConfig] = None

    def __init__(self, **data):
        # In a non-dev deployment (APP_ENV=TEST/PROD on AWS), pull secrets from the
        # AWS secret store and hydrate them into the process environment BEFORE
        # pydantic validates, so the existing env-var aliases below resolve from the
        # store with no per-field plumbing. DEV (default) is a no-op and keeps the
        # plain .env flow. The retriever import is lazy so boto3 stays an AWS-only
        # optional dependency. See qaai/core/secrets.py.
        app_env = os.getenv("APP_ENV", "DEV").upper()
        if app_env in ("TEST", "PROD"):
            from qaai.core.secrets import EnvVariableRetriever
            EnvVariableRetriever.for_environment(app_env).hydrate_environment()
        super().__init__(**data)

    @property
    def telemetry_file_path(self) -> str:
        return str(Path(self.log_file_path).parent / TOKEN_USAGE_JSONL_FILENAME)

    @property
    def prompt_config(self) -> PromptConfig:
        """Get prompt configuration, loading from prompt_set if specified."""
        if self._prompt_config_cache is None:
            if self.prompt_set:
                self._prompt_config_cache = PromptConfig.from_set(self.prompt_set)
            else:
                self._prompt_config_cache = PromptConfig()
        return self._prompt_config_cache
    
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True
    )

# Initialize settings and log configuration
settings = Settings()

# Debug logging
logger.info("=" * 60)
logger.info("QAAI Configuration Loaded")
logger.info("=" * 60)
logger.info("API Key: %s", settings.openai_api_key[:20] + "..." if settings.openai_api_key else "NOT SET")
logger.info("Model: %s", settings.model)
logger.info("Cache Enabled: %s", settings.enable_cache)
logger.info("Cache Dir: %s", settings.cache_dir)
logger.info("JAMA Host: %s", settings.jama_host_address or "NOT SET")
logger.info("PyJama Test Mode: %s", settings.pyjama_test_mode)
logger.info("=" * 60)
