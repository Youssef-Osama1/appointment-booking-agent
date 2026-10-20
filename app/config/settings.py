from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_api_key: SecretStr
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    agent_max_steps: int = Field(default=5, ge=1, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
