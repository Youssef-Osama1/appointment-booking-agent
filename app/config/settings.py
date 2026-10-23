from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


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
    clinic_path: Path = DATA_DIR / "clinic.json"
    database_path: Path = DATA_DIR / "booking.db"
    clinic_timezone: str = "Africa/Cairo"
    now_override: datetime | None = None
    pending_action_ttl_minutes: int = Field(default=10, ge=1, le=60)
    history_max_turns: int = Field(default=10, ge=1, le=50)
    mcp_patient_id: str | None = None

    @field_validator("clinic_timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown timezone {value!r}") from error
        return value

    def current_time(self) -> datetime:
        if self.now_override is not None:
            return self.now_override.replace(tzinfo=None)
        return datetime.now(ZoneInfo(self.clinic_timezone)).replace(tzinfo=None)


@lru_cache
def get_settings() -> Settings:
    return Settings()
