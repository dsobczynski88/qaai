import os
from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from autoqa.utils import make_output_directory
from autoqa.core.constants import (
    DEFAULT_MAX_REQUESTS_PER_MINUTE,
    DEFAULT_MAX_TOKENS_PER_MINUTE,
    DEFAULT_MAX_OUTPUT_TOKENS,
)


class PromptConfig(BaseModel):
    """Jinja2 template filenames used by each LLM node across reviewer graphs."""
    decomposer: str = "decomposer-v4.jinja2"
    summarizer: str = "summarizer-v4.jinja2"  # v4 uses array-only output for better token efficiency
    coverage: str = "coverage_evaluator-v5.jinja2"
    synthesizer: str = "synthesizer-v6.jinja2"
    
    # Hazard reviewer prompts (H1-H7 + final assessor)
    hazard_h1: str = "H1_hazard_record_completeness_and_semantic_integrity.jinja2"
    hazard_h2: str = "H2_software_contribution_and_cause_coverage.jinja2"
    hazard_h3: str = "H3_pre_mitigation_risk_and_exploitability_characterization.jinja2"
    hazard_h4: str = "H4_risk_control_identification_allocation_and_coverage.jinja2"
    hazard_h5: str = "H5_verification_depth_and_hazard_path_effectiveness.jinja2"
    hazard_h6: str = "H6_residual_risk_closure_and_acceptability_decision.jinja2"
    hazard_h7: str = "H7_hsha_update_and_newly_identified_hazard_capture.jinja2"
    hazard_final: str = "hazard_final_assessor-v1.jinja2"

class Settings(BaseSettings):
    """Application settings loaded from environment variables.
    
    Environment Variables:
        API_KEY: API key for the LLM service (required)
        API_BASE_URL: Base URL for the API endpoint (required)
        API_MODEL: Model identifier (required)
        MAX_REQUESTS_PER_MINUTE: Rate limit for API requests (default: 490)
        MAX_TOKENS_PER_MINUTE: Token rate limit (default: 200000)
        MAX_OUTPUT_TOKENS: Maximum output tokens per request (default: 16000)
    """
    openai_api_key: str = Field(..., alias='API_KEY')
    url: str = Field(..., alias='API_BASE_URL')
    model: str = Field(..., alias='API_MODEL')
    max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE
    max_tokens_per_minute: int = DEFAULT_MAX_TOKENS_PER_MINUTE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    log_file_path: str = str(Path(make_output_directory(fold_path="./logs")) / "autoqa.log")
    prompt_config: PromptConfig = Field(default_factory=PromptConfig)
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True
    )

settings = Settings()
