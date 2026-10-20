from pathlib import Path
from typing import Self

from app.schemas.clinic import ClinicData, Doctor, Patient, Specialty


class ClinicRepository:
    def __init__(self, data: ClinicData) -> None:
        self.slot_minutes = data.slot_minutes
        self.seed_appointments = data.appointments
        self._doctors = {doctor.doctor_id: doctor for doctor in data.doctors}
        self._patients = {patient.patient_id: patient for patient in data.patients}

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls(ClinicData.model_validate_json(path.read_text(encoding="utf-8")))

    def find_doctor(self, doctor_id: str) -> Doctor | None:
        return self._doctors.get(doctor_id)

    def list_doctors(self, specialty: Specialty | None = None) -> list[Doctor]:
        return [
            doctor
            for doctor in self._doctors.values()
            if specialty is None or doctor.specialty is specialty
        ]

    def find_patient(self, patient_id: str) -> Patient | None:
        return self._patients.get(patient_id)
