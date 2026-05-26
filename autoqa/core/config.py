import os
from pathlib import Path
from typing import Optional, Union
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from autoqa.utils import make_output_directory
from autoqa.core.constants import (
    DEFAULT_MAX_REQUESTS_PER_MINUTE,
    DEFAULT_MAX_TOKENS_PER_MINUTE,
    DEFAULT_MAX_OUTPUT_TOKENS,
)


class PromptConfig(BaseModel):
    """Jinja2 template paths used by each LLM node across reviewer graphs.
    
    Paths can be either:
    - Legacy flat filenames (for backward compatibility): "decomposer-v4.jinja2"
    - New versioned paths: "decomposer/v5.0.0/template.jinja2"
    
    Use PromptConfig.from_set("set_name") to load from a named prompt set manifest.
    """
    # Test Suite Reviewer prompts
    decomposer: str = "decomposer/v5.0.0/template.jinja2"
    summarizer: str = "summarizer/v4.0.0/template.jinja2"
    design_summarizer: str = "design_summarizer/v1.0.0/template.jinja2"
    coverage: str = "coverage_evaluator/v7.0.0/template.jinja2"
    synthesizer: str = "synthesizer/v8.0.0/template.jinja2"
    
    # Test Case Reviewer prompts
    single_test_aggregator: str = "single_test_aggregator/v6.0.0/template.jinja2"
    single_test_coverage_eval: str = "single_test_coverage_eval/v3.0.0/template.jinja2"
    single_test_logical_steps: str = "single_test_logical_steps/v3.0.0/template.jinja2"
    single_test_prereqs: str = "single_test_prereqs/v3.0.0/template.jinja2"
    
    # Hazard reviewer prompts (H1-H7 + final assessor)
    hazard_h1: str = "hazard_h1/v1.0.0/template.jinja2"
    hazard_h2: str = "hazard_h2/v1.0.0/template.jinja2"
    hazard_h3: str = "hazard_h3/v1.0.0/template.jinja2"
    hazard_h4: str = "hazard_h4/v1.0.0/template.jinja2"
    hazard_h5: str = "hazard_h5/v1.0.0/template.jinja2"
    hazard_h6: str = "hazard_h6/v1.0.0/template.jinja2"
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
        from autoqa.prompts._registry import load_set
        resolved = load_set(set_name)
        
        # Start with current defaults
        current = cls()
        kwargs = current.model_dump()
        
        # Override with resolved prompts from the set
        for role, prompt in resolved.prompts.items():
            # Build relative path: role/version/template.jinja2
            relative_path = f"{prompt.role}/{prompt.version}/template.jinja2"
            kwargs[role] = relative_path
        
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
    openai_api_key: str = Field(..., alias='API_KEY')
    url: Union[str, None] = Field(default=None, alias='API_BASE_URL')
    model: str = Field(..., alias='API_MODEL')
    max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE
    max_tokens_per_minute: int = DEFAULT_MAX_TOKENS_PER_MINUTE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    log_file_path: str = str(Path(make_output_directory(fold_path="./logs")) / "autoqa.log")

    # Token cost rates in USD per million tokens — set in .env to match your model pricing.
    token_cost_input_per_m: float = Field(default=0.15, alias="TOKEN_COST_INPUT_PER_M")
    token_cost_output_per_m: float = Field(default=0.60, alias="TOKEN_COST_OUTPUT_PER_M")

    # Hazard reviewer cache (set ENABLE_HAZARD_CACHE=false to disable entirely)
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")
    hazard_cache_dir: str = Field(default="./cache/hazard", alias="HAZARD_CACHE_DIR")
    enable_hazard_cache: bool = Field(default=True, alias="ENABLE_HAZARD_CACHE")

    # Optional prompt set name - if specified, overrides default prompt_config
    prompt_set: Optional[str] = Field(default=None, alias='PROMPT_SET')
    
    _prompt_config_cache: Optional[PromptConfig] = None
    
    @property
    def telemetry_file_path(self) -> str:
        return str(Path(self.log_file_path).parent / "token_usage.jsonl")

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

settings = Settings()
