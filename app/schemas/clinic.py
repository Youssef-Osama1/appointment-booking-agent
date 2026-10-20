from datetime import date, datetime, time
from enum import StrEnum
from itertools import pairwise
from typing import Self

from pydantic import BaseModel, Field, model_validator


class Specialty(StrEnum):
    DENTISTRY = "dentistry"
    DERMATOLOGY = "dermatology"
    PEDIATRICS = "pediatrics"
    GENERAL = "general"


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"

    @classmethod
    def of(cls, day: date) -> Self:
        return list(cls)[day.weekday()]


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"


class WorkingHours(BaseModel):
    day: Weekday
    start: time
    end: time

    @model_validator(mode="after")
    def check_hours(self) -> Self:
        if self.start >= self.end:
            raise ValueError(f"Working hours on {self.day} end before they start")
        return self


class Doctor(BaseModel):
    doctor_id: str
    name: str
    specialty: Specialty
    schedule: list[WorkingHours] = Field(min_length=1)

    @model_validator(mode="after")
    def check_schedule(self) -> Self:
        blocks = sorted(self.schedule, key=lambda block: (block.day, block.start))
        for previous, current in pairwise(blocks):
            if current.day == previous.day and current.start < previous.end:
                raise ValueError(
                    f"Doctor {self.doctor_id} has overlapping hours on {current.day}"
                )
        return self


class Patient(BaseModel):
    patient_id: str
    name: str


class Appointment(BaseModel):
    appointment_id: int
    patient_id: str
    doctor_id: str
    starts_at: datetime
    status: AppointmentStatus = AppointmentStatus.BOOKED


class ClinicData(BaseModel):
    slot_minutes: int = Field(gt=0, le=240)
    doctors: list[Doctor]
    patients: list[Patient]
    appointments: list[Appointment] = []

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        unique_keys("doctor_id", [doctor.doctor_id for doctor in self.doctors])
        patient_ids = unique_keys("patient_id", [p.patient_id for p in self.patients])
        unique_keys("appointment_id", [a.appointment_id for a in self.appointments])
        doctors = {doctor.doctor_id: doctor for doctor in self.doctors}

        for doctor in self.doctors:
            for block in doctor.schedule:
                if (minutes(block.start) % self.slot_minutes) or (
                    minutes(block.end) % self.slot_minutes
                ):
                    raise ValueError(
                        f"Doctor {doctor.doctor_id} hours on {block.day} are off "
                        f"the {self.slot_minutes}-minute grid"
                    )

        for appointment in self.appointments:
            label = f"Appointment {appointment.appointment_id}"
            if appointment.patient_id not in patient_ids:
                raise ValueError(
                    f"{label} has unknown patient {appointment.patient_id}"
                )
            doctor = doctors.get(appointment.doctor_id)
            if doctor is None:
                raise ValueError(f"{label} has unknown doctor {appointment.doctor_id}")
            if not is_slot_start(doctor, appointment.starts_at, self.slot_minutes):
                raise ValueError(f"{label} is not on {doctor.doctor_id}'s schedule")

        booked = [a for a in self.appointments if a.status is AppointmentStatus.BOOKED]
        unique_keys("doctor slot", [f"{a.doctor_id} {a.starts_at}" for a in booked])
        unique_keys("patient slot", [f"{a.patient_id} {a.starts_at}" for a in booked])
        return self


def minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def is_slot_start(doctor: Doctor, moment: datetime, slot_minutes: int) -> bool:
    if moment.second or moment.microsecond:
        return False
    start = minutes(moment.time())
    day = Weekday.of(moment.date())
    return start % slot_minutes == 0 and any(
        block.day == day
        and minutes(block.start) <= start
        and start + slot_minutes <= minutes(block.end)
        for block in doctor.schedule
    )


def unique_keys[T](label: str, keys: list[T]) -> set[T]:
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate {label}: {duplicates}")
    return set(keys)
