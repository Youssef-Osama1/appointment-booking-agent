import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_reads_values_and_hides_api_key():
    settings = Settings(_env_file=None, llm_api_key="secret-key", llm_model="m")

    assert settings.llm_api_key.get_secret_value() == "secret-key"
    assert settings.llm_model == "m"
    assert "secret-key" not in repr(settings)


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_api_key="k", log_level="INFOO")


@pytest.mark.parametrize("steps", [0, 11])
def test_agent_max_steps_must_stay_in_range(steps):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_api_key="k", agent_max_steps=steps)
