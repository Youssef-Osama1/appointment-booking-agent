from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


def test_paths_point_at_the_data_folder():
    settings = Settings(_env_file=None, llm_api_key="k")

    assert settings.clinic_path.name == "clinic.json"
    assert settings.database_path.name == "booking.db"
    assert settings.clinic_path.parent == settings.database_path.parent
    assert settings.clinic_path.parent.name == "data"
    assert settings.clinic_path.is_file()


def test_booking_defaults():
    settings = Settings(_env_file=None, llm_api_key="k")

    assert settings.clinic_timezone == "Africa/Cairo"
    assert settings.now_override is None
    assert settings.pending_action_ttl_minutes == 10
    assert settings.history_max_turns == 10


def test_now_override_is_returned_as_naive_clinic_time():
    settings = Settings(
        _env_file=None, llm_api_key="k", now_override="2026-10-19T09:00"
    )

    assert settings.current_time() == datetime(2026, 10, 19, 9, 0)


def test_now_override_drops_its_timezone():
    settings = Settings(
        _env_file=None, llm_api_key="k", now_override="2026-10-19T09:00+02:00"
    )

    now = settings.current_time()

    assert now == datetime(2026, 10, 19, 9, 0)
    assert now.tzinfo is None


def test_now_override_reads_from_environment(monkeypatch):
    monkeypatch.setenv("NOW_OVERRIDE", "2026-10-22T16:30")

    settings = Settings(_env_file=None, llm_api_key="k")

    assert settings.current_time() == datetime(2026, 10, 22, 16, 30)


def test_current_time_uses_clinic_timezone_without_override():
    settings = Settings(_env_file=None, llm_api_key="k", clinic_timezone="Asia/Tokyo")

    now = settings.current_time()
    expected = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)

    assert now.tzinfo is None
    assert abs(now - expected) < timedelta(seconds=5)


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "not a zone", "../etc", ""])
def test_unknown_timezone_is_rejected(timezone):
    with pytest.raises(ValidationError, match="Unknown timezone"):
        Settings(_env_file=None, llm_api_key="k", clinic_timezone=timezone)


@pytest.mark.parametrize("minutes", [0, 61])
def test_pending_action_ttl_must_stay_in_range(minutes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_api_key="k", pending_action_ttl_minutes=minutes)


@pytest.mark.parametrize("turns", [0, 51])
def test_history_max_turns_must_stay_in_range(turns):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_api_key="k", history_max_turns=turns)


def test_mcp_patient_id_is_unset_by_default(monkeypatch):
    monkeypatch.delenv("MCP_PATIENT_ID", raising=False)

    assert Settings(_env_file=None, llm_api_key="k").mcp_patient_id is None


def test_mcp_patient_id_reads_from_environment(monkeypatch):
    monkeypatch.setenv("MCP_PATIENT_ID", "P1002")

    assert Settings(_env_file=None, llm_api_key="k").mcp_patient_id == "P1002"
