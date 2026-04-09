from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Loads configuration from environment variables.

    Why this matters:
    - Production config should be injected at runtime (env vars), not hardcoded.
    - Same code/image can run in many environments with different settings.
    """

    # For local dev only: if a .env file exists, load it.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        )

    app_env: str = Field(default="local", alias="APP_ENV")
    app_name: str = Field(default="support-copilot", alias="APP_NAME")
    app_version: str = Field(default="0.0.0", alias="APP_VERSION")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    
    openai_model_input_shield: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL_INPUT_SHIELD")
    openai_model_planner: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL_PLANNER")
    openai_model_response_drafting: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL_RESPONSE_DRAFTING",)
    
    openai_timeout_seconds: float = Field(default=20.0, alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=2, alias="OPENAI_MAX_RETRIES")

    input_shield_temperature: float = Field(default=0.0, alias="INPUT_SHIELD_TEMPERATURE")
    input_shield_max_prompt_chars: int = Field(default=12000, alias="INPUT_SHIELD_MAX_PROMPT_CHARS")

    langsmith_enabled: bool = Field(default=False, alias="LANGSMITH_ENABLED")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Optional: only used when provided (Compose will provide it later)
    redis_url: str | None = Field(default=None, alias="REDIS_URL")



@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings() # pyright: ignore[reportCallIssue]
