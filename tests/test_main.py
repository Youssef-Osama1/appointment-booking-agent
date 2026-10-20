import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
    AgentStepLimitError,
    ConversationNotFoundError,
    LLMOutputError,
    PatientNotFoundError,
)
from app.main import (
    app,
    handle_conflict,
    handle_expired,
    handle_model_failure,
    handle_not_found,
)
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.services.llm_client import LLMClient

THURSDAY_NOON = datetime(2026, 10, 22, 12, 0)


@pytest.fixture
def isolated_settings(settings, tmp_path, monkeypatch):
    isolated = settings.model_copy(update={"database_path": tmp_path / "app.db"})
    monkeypatch.setattr("app.main.get_settings", lambda: isolated)
    return isolated


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "error", "status_code"),
    [
        (handle_model_failure, LLMOutputError("refused"), 502),
        (handle_model_failure, AgentStepLimitError("too many steps"), 502),
        (handle_not_found, PatientNotFoundError("Patient P9 was not found"), 404),
        (handle_not_found, ConversationNotFoundError("Conversation c"), 404),
        (handle_not_found, ActionNotFoundError("Action a was not found"), 404),
        (handle_conflict, ActionConflictError("That time is already booked"), 409),
        (handle_expired, ActionExpiredError("This proposal has expired"), 410),
    ],
)
async def test_errors_map_to_status_codes(handler, error, status_code):
    response = await handler(None, error)

    assert response.status_code == status_code
    assert json.loads(response.body) == {"detail": str(error)}


def test_lifespan_creates_llm_client(isolated_settings):
    with TestClient(app):
        assert isinstance(app.state.llm_client, LLMClient)


def test_lifespan_loads_the_clinic_and_seeds_the_database(isolated_settings):
    with TestClient(app):
        assert isinstance(app.state.clinic, ClinicRepository)
        assert isinstance(app.state.database, Database)
        with app.state.database.connect() as connection:
            assert AppointmentRepository(connection).count() == 7

    assert isolated_settings.database_path.is_file()


def test_lifespan_keeps_existing_bookings_on_restart(isolated_settings):
    with TestClient(app), app.state.database.transaction() as connection:
        AppointmentRepository(connection).create("P1001", "D05", THURSDAY_NOON)

    with TestClient(app), app.state.database.connect() as connection:
        assert AppointmentRepository(connection).count() == 8


def test_lifespan_fails_fast_on_a_broken_clinic_file(isolated_settings, tmp_path):
    broken = tmp_path / "clinic.json"
    broken.write_text('{"slot_minutes": 30}', encoding="utf-8")
    isolated_settings.clinic_path = broken

    with pytest.raises(ValueError, match="validation error"), TestClient(app):
        pass
