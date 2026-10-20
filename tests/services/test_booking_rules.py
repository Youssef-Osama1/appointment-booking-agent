from datetime import datetime
from pathlib import Path

import pytest

from app.core.exceptions import ActionConflictError
from app.repositories.appointment_repository import (
    AppointmentRepository,
    initialize_database,
)
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.services.booking_rules import BookingRules

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = datetime(2026, 10, 19, 9, 0)


@pytest.fixture
def clinic():
    return ClinicRepository.from_file(CLINIC_PATH)


@pytest.fixture
def appointments(tmp_path, clinic):
    database = Database(tmp_path / "booking.db")
    initialize_database(database, clinic.seed_appointments)
    with database.connect() as connection:
        yield AppointmentRepository(connection)


@pytest.fixture
def make_rules(clinic, appointments):
    def make(patient_id="P1001", now=NOW):
        return BookingRules(clinic, appointments, patient_id, now)

    return make


def thursday(hour, minute=0):
    return datetime(2026, 10, 22, hour, minute)


def test_check_booking_returns_the_doctor(make_rules):
    doctor = make_rules().check_booking("D01", thursday(10))

    assert doctor.doctor_id == "D01"


@pytest.mark.parametrize(
    "starts_at",
    [thursday(9), thursday(12, 30), datetime(2026, 10, 20, 11, 0)],
)
def test_check_booking_accepts_free_slots(make_rules, starts_at):
    doctor_id = "D05" if starts_at.day == 20 else "D01"

    assert make_rules().check_booking(doctor_id, starts_at).doctor_id == doctor_id


@pytest.mark.parametrize(
    ("doctor_id", "starts_at", "message"),
    [
        ("D99", thursday(10), "Unknown doctor D99"),
        ("d01", thursday(10), "Unknown doctor d01"),
        ("D01", datetime(2026, 10, 17, 9, 0), "already passed"),
        ("D01", NOW, "already passed"),
        ("D01", thursday(8, 30), "Dr. Karim Adel has no appointment slot at"),
        ("D01", thursday(13), "has no appointment slot at Thursday 13:00"),
        ("D01", thursday(10, 15), "has no appointment slot"),
        ("D01", datetime(2026, 10, 23, 10, 0), "has no appointment slot at Friday"),
        ("D01", thursday(10, 30), "That time is already booked"),
        ("D01", thursday(11), "That time is already booked"),
    ],
)
def test_check_booking_rejects(make_rules, doctor_id, starts_at, message):
    with pytest.raises(ActionConflictError, match=message):
        make_rules().check_booking(doctor_id, starts_at)


def test_check_booking_rejects_a_patient_double_booking(make_rules, appointments):
    appointments.create("P1001", "D05", thursday(12))

    with pytest.raises(ActionConflictError, match="You already have an appointment"):
        make_rules().check_booking("D01", thursday(12))


def test_check_booking_reports_the_doctor_before_the_patient(make_rules):
    with pytest.raises(ActionConflictError, match="already booked"):
        make_rules("P1002").check_booking("D01", thursday(10, 30))


def test_check_booking_allows_another_patient_at_the_same_time(make_rules):
    assert make_rules("P1002").check_booking("D01", datetime(2026, 10, 24, 11, 0))


def test_check_booking_skips_slots_that_started_today(make_rules):
    rules = make_rules(now=datetime(2026, 10, 22, 10, 5))

    with pytest.raises(ActionConflictError, match="already passed"):
        rules.check_booking("D01", thursday(10))
    assert rules.check_booking("D01", thursday(11, 30)).doctor_id == "D01"


def test_check_cancellation_returns_the_appointment(make_rules):
    appointment = make_rules().check_cancellation(1002)

    assert appointment.appointment_id == 1002
    assert appointment.doctor_id == "D03"


@pytest.mark.parametrize(
    ("patient_id", "appointment_id"),
    [
        ("P1001", 1003),
        ("P1001", 1001),
        ("P1001", 1006),
        ("P1001", 9999),
        ("P1002", 1002),
    ],
)
def test_check_cancellation_hides_why_it_fails(make_rules, patient_id, appointment_id):
    expected = f"Appointment {appointment_id} is not one of your upcoming appointments"

    with pytest.raises(ActionConflictError) as error:
        make_rules(patient_id).check_cancellation(appointment_id)

    assert str(error.value) == expected


def test_check_cancellation_rejects_an_appointment_that_started(make_rules):
    rules = make_rules(now=datetime(2026, 10, 24, 11, 0))

    with pytest.raises(ActionConflictError, match="not one of your upcoming"):
        rules.check_cancellation(1002)


def test_check_reschedule_returns_the_current_appointment(make_rules):
    appointment = make_rules().check_reschedule(1002, datetime(2026, 10, 27, 12, 0))

    assert appointment.starts_at == datetime(2026, 10, 24, 11, 0)


@pytest.mark.parametrize(
    ("starts_at", "message"),
    [
        (datetime(2026, 10, 24, 11, 0), "already at that time"),
        (datetime(2026, 10, 22, 10, 0), "Dr. Ahmed Farouk has no appointment slot"),
        (datetime(2026, 10, 24, 14, 0), "has no appointment slot"),
        (datetime(2026, 10, 18, 11, 0), "already passed"),
    ],
)
def test_check_reschedule_rejects(make_rules, starts_at, message):
    with pytest.raises(ActionConflictError, match=message):
        make_rules().check_reschedule(1002, starts_at)


def test_check_reschedule_keeps_the_same_doctor(make_rules, appointments):
    appointments.create("P1002", "D03", datetime(2026, 10, 27, 12, 0))

    with pytest.raises(ActionConflictError, match="already booked"):
        make_rules().check_reschedule(1002, datetime(2026, 10, 27, 12, 0))


def test_check_reschedule_rejects_a_patient_double_booking(make_rules, appointments):
    appointments.create("P1001", "D05", datetime(2026, 10, 27, 12, 0))

    with pytest.raises(ActionConflictError, match="You already have an appointment"):
        make_rules().check_reschedule(1002, datetime(2026, 10, 27, 12, 0))


@pytest.mark.parametrize(("patient_id", "appointment_id"), [("P1001", 1003)])
def test_check_reschedule_hides_other_patients(make_rules, patient_id, appointment_id):
    with pytest.raises(ActionConflictError, match="not one of your upcoming"):
        make_rules(patient_id).check_reschedule(
            appointment_id, datetime(2026, 10, 22, 9, 0)
        )
