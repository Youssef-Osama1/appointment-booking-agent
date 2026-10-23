from datetime import datetime
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import initialize_database
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.services.action_service import ActionService
from app.tools.booking import PatientTools

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = datetime(2026, 10, 19, 9, 0)


@pytest.fixture
def clinic():
    return ClinicRepository.from_file(CLINIC_PATH)


@pytest.fixture
def database(tmp_path, clinic):
    database = Database(tmp_path / "booking.db")
    initialize_database(database, clinic.seed_appointments)
    return database


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        llm_api_key="k",
        now_override=NOW,
        mcp_patient_id="P1001",
    )


@pytest.fixture
def open_conversation(database):
    def open_(patient_id="P1001"):
        with database.connect() as connection:
            return ConversationRepository(connection).create(patient_id, NOW)

    return open_


@pytest.fixture
def conversation_id(open_conversation):
    return open_conversation()


@pytest.fixture
def make_tools(clinic, database, settings):
    def make(conversation_id, patient_id="P1001"):
        return PatientTools(
            clinic,
            database,
            ActionService(database, clinic, settings),
            clinic.find_patient(patient_id),
            conversation_id,
            NOW,
        )

    return make


@pytest.fixture
def rows(database):
    def read(sql, *params):
        with database.connect() as connection:
            return [tuple(row) for row in connection.execute(sql, params)]

    return read


@pytest.fixture
def pending(database):
    def find(conversation_id):
        with database.connect() as connection:
            return ActionRepository(connection).find_pending(conversation_id)

    return find
