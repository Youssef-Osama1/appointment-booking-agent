from pathlib import Path

import pytest
from pydantic import ValidationError

from app.repositories.clinic_repository import ClinicRepository
from app.schemas.clinic import ClinicData, Specialty

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"


@pytest.fixture
def repository():
    return ClinicRepository.from_file(CLINIC_PATH)


def test_from_file_loads_the_clinic(repository):
    assert repository.slot_minutes == 30
    assert len(repository.seed_appointments) == 7
    assert repository.seed_appointments[0].appointment_id == 1001


def test_from_file_rejects_invalid_data(tmp_path):
    path = tmp_path / "clinic.json"
    path.write_text('{"slot_minutes": 0, "doctors": [], "patients": []}')

    with pytest.raises(ValidationError):
        ClinicRepository.from_file(path)


def test_find_doctor(repository):
    doctor = repository.find_doctor("D01")

    assert doctor.name == "Dr. Karim Adel"
    assert doctor.specialty is Specialty.DENTISTRY


@pytest.mark.parametrize("doctor_id", ["D99", "d01", ""])
def test_find_doctor_returns_none_for_unknown_id(repository, doctor_id):
    assert repository.find_doctor(doctor_id) is None


def test_list_doctors_returns_everyone_by_default(repository):
    doctors = repository.list_doctors()

    assert [doctor.doctor_id for doctor in doctors] == [
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
    ]


def test_list_doctors_filters_by_specialty(repository):
    dentists = repository.list_doctors(Specialty.DENTISTRY)

    assert [doctor.doctor_id for doctor in dentists] == ["D01", "D02"]


def test_list_doctors_returns_empty_when_no_one_matches():
    data = ClinicData.model_validate(
        {
            "slot_minutes": 30,
            "doctors": [
                {
                    "doctor_id": "D1",
                    "name": "Dr. A",
                    "specialty": "general",
                    "schedule": [{"day": "monday", "start": "09:00", "end": "10:00"}],
                }
            ],
            "patients": [],
        }
    )

    assert ClinicRepository(data).list_doctors(Specialty.PEDIATRICS) == []


def test_find_patient(repository):
    assert repository.find_patient("P1001").name == "Omar Khaled"


@pytest.mark.parametrize("patient_id", ["P9999", "p1001", ""])
def test_find_patient_returns_none_for_unknown_id(repository, patient_id):
    assert repository.find_patient(patient_id) is None
