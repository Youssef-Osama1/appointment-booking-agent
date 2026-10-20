from datetime import date, datetime, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.clinic import (
    AppointmentStatus,
    ClinicData,
    Doctor,
    Specialty,
    Weekday,
    WorkingHours,
    is_slot_start,
    minutes,
)

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"


def make_doctor(**overrides):
    doctor = {
        "doctor_id": "D1",
        "name": "Dr. A",
        "specialty": "dentistry",
        "schedule": [{"day": "thursday", "start": "09:00", "end": "13:00"}],
    }
    return doctor | overrides


def make_appointment(**overrides):
    appointment = {
        "appointment_id": 1,
        "patient_id": "P1",
        "doctor_id": "D1",
        "starts_at": "2026-10-22T10:00",
    }
    return appointment | overrides


def make_clinic(**overrides):
    clinic = {
        "slot_minutes": 30,
        "doctors": [
            make_doctor(),
            make_doctor(
                doctor_id="D2",
                specialty="general",
                schedule=[{"day": "thursday", "start": "10:00", "end": "12:00"}],
            ),
        ],
        "patients": [
            {"patient_id": "P1", "name": "Patient One"},
            {"patient_id": "P2", "name": "Patient Two"},
        ],
        "appointments": [make_appointment()],
    }
    return clinic | overrides


def test_clinic_file_is_valid():
    data = ClinicData.model_validate_json(CLINIC_PATH.read_text(encoding="utf-8"))

    assert data.slot_minutes == 30
    assert len(data.doctors) == 5
    assert len(data.patients) == 3
    assert len(data.appointments) == 7


def test_clinic_file_covers_every_specialty():
    data = ClinicData.model_validate_json(CLINIC_PATH.read_text(encoding="utf-8"))

    assert {doctor.specialty for doctor in data.doctors} == set(Specialty)


def test_clinic_file_keeps_friday_closed():
    data = ClinicData.model_validate_json(CLINIC_PATH.read_text(encoding="utf-8"))

    days = {block.day for doctor in data.doctors for block in doctor.schedule}
    assert Weekday.FRIDAY not in days


def test_minimal_clinic_is_valid():
    data = ClinicData.model_validate(make_clinic())

    appointment = data.appointments[0]
    assert appointment.status is AppointmentStatus.BOOKED
    assert appointment.starts_at == datetime(2026, 10, 22, 10, 0)
    assert data.doctors[0].specialty is Specialty.DENTISTRY
    assert data.doctors[0].schedule[0].start == time(9, 0)


def test_appointments_default_to_empty():
    clinic = make_clinic()
    del clinic["appointments"]

    assert ClinicData.model_validate(clinic).appointments == []


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 19), Weekday.MONDAY),
        (date(2026, 10, 22), Weekday.THURSDAY),
        (date(2026, 10, 23), Weekday.FRIDAY),
        (date(2026, 10, 24), Weekday.SATURDAY),
        (date(2026, 10, 25), Weekday.SUNDAY),
    ],
)
def test_weekday_of_date(day, expected):
    assert Weekday.of(day) is expected


def test_minutes_counts_from_midnight():
    assert minutes(time(0, 0)) == 0
    assert minutes(time(9, 30)) == 570


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"start": "13:00", "end": "09:00"}, "end before they start"),
        ({"start": "09:00", "end": "09:00"}, "end before they start"),
        ({"day": "someday"}, "Input should be"),
    ],
)
def test_invalid_working_hours_are_rejected(overrides, message):
    hours = {"day": "monday", "start": "09:00", "end": "13:00"} | overrides

    with pytest.raises(ValidationError, match=message):
        WorkingHours.model_validate(hours)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schedule": []}, "at least 1 item"),
        ({"specialty": "cardiology"}, "Input should be"),
        (
            {
                "schedule": [
                    {"day": "monday", "start": "09:00", "end": "12:00"},
                    {"day": "monday", "start": "11:30", "end": "14:00"},
                ]
            },
            "overlapping hours on monday",
        ),
    ],
)
def test_invalid_doctor_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Doctor.model_validate(make_doctor(**overrides))


def test_doctor_allows_separate_blocks_on_one_day():
    doctor = Doctor.model_validate(
        make_doctor(
            schedule=[
                {"day": "wednesday", "start": "17:00", "end": "20:00"},
                {"day": "wednesday", "start": "09:00", "end": "12:00"},
            ]
        )
    )

    assert len(doctor.schedule) == 2


def test_doctor_allows_back_to_back_blocks():
    doctor = Doctor.model_validate(
        make_doctor(
            schedule=[
                {"day": "monday", "start": "09:00", "end": "12:00"},
                {"day": "monday", "start": "12:00", "end": "14:00"},
            ]
        )
    )

    assert len(doctor.schedule) == 2


def test_doctor_allows_same_hours_on_different_days():
    doctor = Doctor.model_validate(
        make_doctor(
            schedule=[
                {"day": "monday", "start": "09:00", "end": "12:00"},
                {"day": "tuesday", "start": "09:00", "end": "12:00"},
            ]
        )
    )

    assert len(doctor.schedule) == 2


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 22, 9, 0), True),
        (datetime(2026, 10, 22, 10, 30), True),
        (datetime(2026, 10, 22, 12, 30), True),
        (datetime(2026, 10, 22, 13, 0), False),
        (datetime(2026, 10, 22, 8, 30), False),
        (datetime(2026, 10, 22, 10, 15), False),
        (datetime(2026, 10, 22, 10, 0, 30), False),
        (datetime(2026, 10, 22, 10, 0, 0, 1), False),
        (datetime(2026, 10, 21, 10, 0), False),
        (datetime(2026, 10, 23, 10, 0), False),
    ],
)
def test_is_slot_start(moment, expected):
    doctor = Doctor.model_validate(make_doctor())

    assert is_slot_start(doctor, moment, 30) is expected


def test_is_slot_start_needs_the_whole_slot_inside_hours():
    doctor = Doctor.model_validate(make_doctor())

    assert is_slot_start(doctor, datetime(2026, 10, 22, 12, 0), 60) is True
    assert is_slot_start(doctor, datetime(2026, 10, 22, 12, 30), 60) is False


def test_is_slot_start_checks_every_block():
    doctor = Doctor.model_validate(
        make_doctor(
            schedule=[
                {"day": "wednesday", "start": "09:00", "end": "12:00"},
                {"day": "wednesday", "start": "17:00", "end": "20:00"},
            ]
        )
    )

    assert is_slot_start(doctor, datetime(2026, 10, 21, 17, 30), 30) is True
    assert is_slot_start(doctor, datetime(2026, 10, 21, 14, 0), 30) is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"slot_minutes": 0}, "greater than 0"),
        ({"slot_minutes": 241}, "less than or equal to 240"),
        (
            {"doctors": [make_doctor(), make_doctor()]},
            r"Duplicate doctor_id: \['D1'\]",
        ),
        (
            {
                "patients": [
                    {"patient_id": "P1", "name": "A"},
                    {"patient_id": "P1", "name": "B"},
                ]
            },
            r"Duplicate patient_id: \['P1'\]",
        ),
        (
            {
                "appointments": [
                    make_appointment(),
                    make_appointment(starts_at="2026-10-22T11:00"),
                ]
            },
            r"Duplicate appointment_id: \[1\]",
        ),
        (
            {"appointments": [make_appointment(patient_id="P9")]},
            "Appointment 1 has unknown patient P9",
        ),
        (
            {"appointments": [make_appointment(doctor_id="D9")]},
            "Appointment 1 has unknown doctor D9",
        ),
        (
            {"appointments": [make_appointment(starts_at="2026-10-22T10:15")]},
            "Appointment 1 is not on D1's schedule",
        ),
        (
            {"appointments": [make_appointment(starts_at="2026-10-23T10:00")]},
            "Appointment 1 is not on D1's schedule",
        ),
        (
            {"appointments": [make_appointment(status="pending")]},
            "Input should be",
        ),
    ],
)
def test_invalid_clinic_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        ClinicData.model_validate(make_clinic(**overrides))


@pytest.mark.parametrize(
    ("start", "end"),
    [("09:15", "13:00"), ("09:00", "12:45")],
)
def test_hours_off_the_slot_grid_are_rejected(start, end):
    doctor = make_doctor(schedule=[{"day": "thursday", "start": start, "end": end}])

    with pytest.raises(ValidationError, match="off the 30-minute grid"):
        ClinicData.model_validate(make_clinic(doctors=[doctor], appointments=[]))


def test_two_bookings_for_one_doctor_slot_are_rejected():
    appointments = [
        make_appointment(),
        make_appointment(appointment_id=2, patient_id="P2"),
    ]

    with pytest.raises(ValidationError, match="Duplicate doctor slot"):
        ClinicData.model_validate(make_clinic(appointments=appointments))


def test_one_patient_booked_with_two_doctors_at_once_is_rejected():
    appointments = [
        make_appointment(),
        make_appointment(appointment_id=2, doctor_id="D2"),
    ]

    with pytest.raises(ValidationError, match="Duplicate patient slot"):
        ClinicData.model_validate(make_clinic(appointments=appointments))


def test_cancelled_appointment_frees_its_slot():
    appointments = [
        make_appointment(status="cancelled"),
        make_appointment(appointment_id=2, patient_id="P2"),
        make_appointment(appointment_id=3, doctor_id="D2", status="cancelled"),
    ]

    data = ClinicData.model_validate(make_clinic(appointments=appointments))

    assert [a.status for a in data.appointments] == [
        AppointmentStatus.CANCELLED,
        AppointmentStatus.BOOKED,
        AppointmentStatus.CANCELLED,
    ]
