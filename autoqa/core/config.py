import os
from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from autoqa.utils import make_output_directory


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
    openai_api_key: str = Field(..., alias='BEDROCK_API_KEY')
    url: str = Field(..., alias='BEDROCK_API_BASE_URL')
    model: str = Field(..., alias='BEDROCK_MODEL')
    max_requests_per_minute: int = 490
    max_tokens_per_minute: int = 200000
    max_output_tokens: int = 16000  # Maximum output tokens for LLM (Haiku supports up to 16K)
    log_file_path: str = str(Path(make_output_directory(fold_path="./logs")) / "autoqa.log")
    prompt_config: PromptConfig = Field(default_factory=PromptConfig)
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True
    )

settings = Settings()
