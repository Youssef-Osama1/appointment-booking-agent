import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from app.repositories.appointment_repository import (
    AppointmentRepository,
    db_time,
    initialize_database,
)
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.schemas.clinic import Appointment, AppointmentStatus

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = datetime(2026, 10, 19, 9, 0)
THURSDAY = date(2026, 10, 22)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "booking.db")
    seed = ClinicRepository.from_file(CLINIC_PATH).seed_appointments
    initialize_database(database, seed)
    return database


@pytest.fixture
def repository(database):
    with database.connect() as connection:
        yield AppointmentRepository(connection)


def statuses(database):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT appointment_id, starts_at, status FROM appointments "
            "ORDER BY appointment_id"
        )
        return [tuple(row) for row in rows]


def test_db_time_keeps_minutes_only():
    assert db_time(datetime(2026, 10, 22, 10, 0)) == "2026-10-22T10:00"
    assert db_time(datetime(2026, 10, 22, 10, 0, 59, 9)) == "2026-10-22T10:00"


def test_initialize_database_seeds_the_clinic(repository):
    assert repository.count() == 7


def test_initialize_database_does_not_seed_twice(database, repository):
    repository.create("P1003", "D05", datetime(2026, 10, 20, 10, 0))
    seed = ClinicRepository.from_file(CLINIC_PATH).seed_appointments

    initialize_database(database, seed)

    assert repository.count() == 8


def test_seed_keeps_ids_times_and_statuses(database):
    rows = statuses(database)

    assert rows[0] == (1001, "2026-10-17T09:00", "booked")
    assert rows[5] == (1006, "2026-10-20T11:00", "cancelled")
    assert [row[0] for row in rows] == list(range(1001, 1008))


def test_find_returns_the_patients_appointment(repository):
    appointment = repository.find(1002, "P1001")

    assert appointment == Appointment(
        appointment_id=1002,
        patient_id="P1001",
        doctor_id="D03",
        starts_at=datetime(2026, 10, 24, 11, 0),
    )


def test_find_returns_cancelled_appointments_too(repository):
    assert repository.find(1006, "P1001").status is AppointmentStatus.CANCELLED


@pytest.mark.parametrize(
    ("appointment_id", "patient_id"),
    [(1003, "P1001"), (1002, "P1002"), (9999, "P1001"), (1002, "")],
)
def test_find_hides_other_patients_and_unknown_ids(
    repository, appointment_id, patient_id
):
    assert repository.find(appointment_id, patient_id) is None


def test_list_upcoming_skips_past_and_cancelled(repository):
    upcoming = repository.list_upcoming("P1001", NOW)

    assert [a.appointment_id for a in upcoming] == [1002]


def test_list_upcoming_orders_by_time(repository):
    repository.create("P1002", "D05", datetime(2026, 10, 20, 10, 0))

    upcoming = repository.list_upcoming("P1002", NOW)

    assert [a.starts_at for a in upcoming] == [
        datetime(2026, 10, 20, 10, 0),
        datetime(2026, 10, 22, 10, 30),
        datetime(2026, 10, 22, 17, 0),
    ]


def test_list_upcoming_includes_a_slot_starting_now(repository):
    upcoming = repository.list_upcoming("P1001", datetime(2026, 10, 24, 11, 0))

    assert [a.appointment_id for a in upcoming] == [1002]


def test_list_upcoming_is_empty_for_unknown_patient(repository):
    assert repository.list_upcoming("P9999", NOW) == []


def test_booked_times_returns_one_doctors_day(repository):
    assert repository.booked_times("D01", THURSDAY) == {
        datetime(2026, 10, 22, 10, 30),
        datetime(2026, 10, 22, 11, 0),
    }


def test_booked_times_ignores_cancelled_and_other_days(repository):
    assert repository.booked_times("D05", date(2026, 10, 20)) == set()
    assert repository.booked_times("D01", date(2026, 10, 21)) == set()


def test_booked_times_includes_midnight_and_excludes_next_day(repository):
    repository.create("P1001", "D09", datetime(2026, 10, 22, 0, 0))
    repository.create("P1002", "D09", datetime(2026, 10, 23, 0, 0))

    assert repository.booked_times("D09", THURSDAY) == {datetime(2026, 10, 22, 0, 0)}


def test_is_doctor_booked(repository):
    assert repository.is_doctor_booked("D01", datetime(2026, 10, 22, 10, 30))
    assert not repository.is_doctor_booked("D01", datetime(2026, 10, 22, 10, 0))
    assert not repository.is_doctor_booked("D05", datetime(2026, 10, 20, 11, 0))


def test_is_patient_booked(repository):
    assert repository.is_patient_booked("P1001", datetime(2026, 10, 24, 11, 0))
    assert not repository.is_patient_booked("P1002", datetime(2026, 10, 24, 11, 0))
    assert not repository.is_patient_booked("P1001", datetime(2026, 10, 20, 11, 0))


def test_create_returns_the_new_appointment(repository):
    appointment = repository.create("P1001", "D01", datetime(2026, 10, 22, 9, 0))

    assert appointment.appointment_id == 1008
    assert appointment.status is AppointmentStatus.BOOKED
    assert repository.find(1008, "P1001") == appointment


def test_create_stores_minutes_only(database, repository):
    repository.create("P1001", "D01", datetime(2026, 10, 22, 9, 0, 30))

    assert statuses(database)[-1] == (1008, "2026-10-22T09:00", "booked")
    assert repository.is_doctor_booked("D01", datetime(2026, 10, 22, 9, 0))


def test_create_rejects_a_taken_doctor_slot(repository):
    with pytest.raises(sqlite3.IntegrityError):
        repository.create("P1001", "D01", datetime(2026, 10, 22, 10, 30))


def test_create_rejects_a_patient_double_booking(repository):
    with pytest.raises(sqlite3.IntegrityError):
        repository.create("P1001", "D05", datetime(2026, 10, 24, 11, 0))


def test_create_reuses_a_cancelled_slot(repository):
    appointment = repository.create("P1002", "D05", datetime(2026, 10, 20, 11, 0))

    assert appointment.appointment_id == 1008


def test_cancel_marks_the_appointment_cancelled(repository):
    assert repository.cancel(1002, "P1001") is True

    assert repository.find(1002, "P1001").status is AppointmentStatus.CANCELLED
    assert not repository.is_doctor_booked("D03", datetime(2026, 10, 24, 11, 0))


@pytest.mark.parametrize(
    ("appointment_id", "patient_id"),
    [(1003, "P1001"), (1006, "P1001"), (9999, "P1001")],
)
def test_cancel_touches_nothing_it_does_not_own(
    database, repository, appointment_id, patient_id
):
    before = statuses(database)

    assert repository.cancel(appointment_id, patient_id) is False

    assert statuses(database) == before


def test_cancel_twice_only_succeeds_once(repository):
    assert repository.cancel(1002, "P1001") is True
    assert repository.cancel(1002, "P1001") is False


def test_reschedule_moves_the_appointment(repository):
    new_time = datetime(2026, 10, 27, 12, 0)

    assert repository.reschedule(1002, "P1001", new_time) is True

    assert repository.find(1002, "P1001").starts_at == new_time
    assert not repository.is_doctor_booked("D03", datetime(2026, 10, 24, 11, 0))


@pytest.mark.parametrize(
    ("appointment_id", "patient_id"),
    [(1003, "P1001"), (1006, "P1001"), (9999, "P1001")],
)
def test_reschedule_touches_nothing_it_does_not_own(
    database, repository, appointment_id, patient_id
):
    before = statuses(database)

    moved = repository.reschedule(
        appointment_id, patient_id, datetime(2026, 10, 27, 12, 0)
    )

    assert moved is False
    assert statuses(database) == before


def test_reschedule_into_a_taken_slot_is_rejected(database, repository):
    before = statuses(database)

    with pytest.raises(sqlite3.IntegrityError):
        repository.reschedule(1003, "P1002", datetime(2026, 10, 22, 11, 0))

    assert statuses(database) == before
