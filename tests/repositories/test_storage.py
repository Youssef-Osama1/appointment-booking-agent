from pathlib import Path

from app.config.settings import Settings
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.storage import open_storage

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"


def make_settings(tmp_path):
    return Settings(
        _env_file=None,
        llm_api_key="k",
        clinic_path=CLINIC_PATH,
        database_path=tmp_path / "data" / "booking.db",
    )


def test_open_storage_loads_the_clinic_and_seeds_the_database(tmp_path):
    clinic, database = open_storage(make_settings(tmp_path))

    assert clinic.find_doctor("D01").name == "Dr. Karim Adel"
    with database.connect() as connection:
        assert AppointmentRepository(connection).count() == len(
            clinic.seed_appointments
        )


def test_open_storage_twice_does_not_seed_again(tmp_path):
    settings = make_settings(tmp_path)
    open_storage(settings)

    clinic, database = open_storage(settings)

    with database.connect() as connection:
        assert AppointmentRepository(connection).count() == len(
            clinic.seed_appointments
        )
