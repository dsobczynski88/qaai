import os
from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from autoqa.utils import make_output_directory


class PromptConfig(BaseModel):
    """Jinja2 template filenames used by each LLM node across reviewer graphs."""
    decomposer: str = "decomposer-v4.jinja2"
    summarizer: str = "summarizer-v2.jinja2"
    coverage: str = "coverage_evaluator-v5.jinja2"
    synthesizer: str = "synthesizer-v6.jinja2"
    hazard_h1: str = "hazard_h1_evaluator-v1.jinja2"
    hazard_h2: str = "hazard_h2_evaluator-v1.jinja2"
    hazard_h3: str = "hazard_h3_evaluator-v1.jinja2"
    hazard_h4: str = "hazard_h4_evaluator-v1.jinja2"
    hazard_h5: str = "hazard_h5_evaluator-v1.jinja2"
    hazard_final: str = "hazard_final_assessor-v1.jinja2"

class Settings(BaseSettings):
    openai_api_key: str = Field(..., alias='BEDROCK_API_KEY')
    url: str = Field(..., alias='BEDROCK_API_BASE_URL')
    model: str = Field(..., alias='BEDROCK_MODEL')
    max_requests_per_minute: int = 490
    max_tokens_per_minute: int = 200000
    log_file_path: str = str(Path(make_output_directory(fold_path="./logs")) / "autoqa.log")
    prompt_config: PromptConfig = Field(default_factory=PromptConfig)
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True
    )

settings = Settings()
