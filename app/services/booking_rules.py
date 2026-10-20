from datetime import datetime

from app.core.exceptions import ActionConflictError
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.schemas.clinic import Appointment, AppointmentStatus, Doctor, is_slot_start


class BookingRules:
    def __init__(
        self,
        clinic: ClinicRepository,
        appointments: AppointmentRepository,
        patient_id: str,
        now: datetime,
    ) -> None:
        self._clinic = clinic
        self._appointments = appointments
        self._patient_id = patient_id
        self._now = now

    def check_booking(self, doctor_id: str, starts_at: datetime) -> Doctor:
        doctor = self._clinic.find_doctor(doctor_id)
        if doctor is None:
            raise ActionConflictError(f"Unknown doctor {doctor_id}")
        self._check_slot(doctor, starts_at)
        return doctor

    def check_cancellation(self, appointment_id: int) -> Appointment:
        return self._upcoming(appointment_id)

    def check_reschedule(self, appointment_id: int, starts_at: datetime) -> Appointment:
        appointment = self._upcoming(appointment_id)
        if starts_at == appointment.starts_at:
            raise ActionConflictError("The appointment is already at that time")
        self._check_slot(self._clinic.find_doctor(appointment.doctor_id), starts_at)
        return appointment

    def _upcoming(self, appointment_id: int) -> Appointment:
        appointment = self._appointments.find(appointment_id, self._patient_id)
        if (
            appointment is None
            or appointment.status is not AppointmentStatus.BOOKED
            or appointment.starts_at <= self._now
        ):
            raise ActionConflictError(
                f"Appointment {appointment_id} is not one of your upcoming appointments"
            )
        return appointment

    def _check_slot(self, doctor: Doctor, starts_at: datetime) -> None:
        if starts_at <= self._now:
            raise ActionConflictError("That time has already passed")
        if not is_slot_start(doctor, starts_at, self._clinic.slot_minutes):
            raise ActionConflictError(
                f"{doctor.name} has no appointment slot at {starts_at:%A %H:%M}"
            )
        if self._appointments.is_doctor_booked(doctor.doctor_id, starts_at):
            raise ActionConflictError("That time is already booked")
        if self._appointments.is_patient_booked(self._patient_id, starts_at):
            raise ActionConflictError("You already have an appointment at that time")
