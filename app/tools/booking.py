from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.core.exceptions import ActionConflictError, ToolError
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.schemas.actions import (
    BookProposal,
    CancelProposal,
    PendingAction,
    Proposal,
    RescheduleProposal,
)
from app.schemas.clinic import Appointment, Doctor, Patient, Specialty, Weekday
from app.services.action_service import ActionService

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)
]
DoctorId = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=10)
]
DAY_HELP = "today, tomorrow, an English weekday name or a YYYY-MM-DD date"
SPECIALTY_HELP = "dentistry, dermatology, pediatrics or general"
NOON = time(12, 0)
EVENING = time(17, 0)


class PartOfDay(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    ANY = "any"

    def includes(self, moment: time) -> bool:
        match self:
            case PartOfDay.MORNING:
                return moment < NOON
            case PartOfDay.AFTERNOON:
                return NOON <= moment < EVENING
            case PartOfDay.EVENING:
                return moment >= EVENING
            case PartOfDay.ANY:
                return True


class ListDoctorsArgs(BaseModel):
    specialty: ShortText | None = Field(
        default=None, description=f"{SPECIALTY_HELP}, or omit for all doctors"
    )


class FindSlotsArgs(BaseModel):
    day: ShortText = Field(description=DAY_HELP)
    specialty: ShortText | None = Field(
        default=None, description=f"Search every doctor of: {SPECIALTY_HELP}"
    )
    doctor_id: DoctorId | None = Field(
        default=None, description="Search one doctor, e.g. D01"
    )
    part_of_day: ShortText = Field(
        default="any",
        description="morning (before 12:00), afternoon (12:00-17:00), evening or any",
    )


class GetMyAppointmentsArgs(BaseModel):
    pass


class ProposeBookingArgs(BaseModel):
    doctor_id: DoctorId = Field(description="Doctor ID from the tools, e.g. D01")
    day: ShortText = Field(description=DAY_HELP)
    start: ShortText = Field(description="Start time in 24-hour HH:MM, e.g. 10:00")


class ProposeCancellationArgs(BaseModel):
    appointment_id: int = Field(description="Appointment ID from get_my_appointments")


class ProposeRescheduleArgs(BaseModel):
    appointment_id: int = Field(description="Appointment ID from get_my_appointments")
    day: ShortText = Field(description=f"New day: {DAY_HELP}")
    start: ShortText = Field(description="New start time in 24-hour HH:MM, e.g. 10:00")


class PatientTools:
    def __init__(
        self,
        clinic: ClinicRepository,
        database: Database,
        actions: ActionService,
        patient: Patient,
        conversation_id: str,
        now: datetime,
    ) -> None:
        self._clinic = clinic
        self._database = database
        self._actions = actions
        self._patient = patient
        self._conversation_id = conversation_id
        self._now = now

    def list_doctors(self, specialty: str | None = None) -> dict:
        doctors = self._clinic.list_doctors(parse_specialty(specialty))
        return {"doctors": [doctor_summary(doctor) for doctor in doctors]}

    def find_slots(
        self,
        day: str,
        specialty: str | None = None,
        doctor_id: str | None = None,
        part_of_day: str = "any",
    ) -> dict:
        if doctor_id is None and specialty is None:
            raise ToolError("Give a specialty or a doctor_id")
        target = resolve_day(day, self._now.date())
        part = parse_part_of_day(part_of_day)
        doctors = (
            [self._doctor(doctor_id)]
            if doctor_id
            else self._clinic.list_doctors(parse_specialty(specialty))
        )
        with self._database.connect() as connection:
            appointments = AppointmentRepository(connection)
            results = [
                {
                    "doctor_id": doctor.doctor_id,
                    "name": doctor.name,
                    "free_times": self._free_times(
                        doctor,
                        target,
                        appointments.booked_times(doctor.doctor_id, target),
                        part,
                    ),
                }
                for doctor in doctors
                if any(block.day == Weekday.of(target) for block in doctor.schedule)
            ]
        return day_ref(target) | {"part_of_day": part, "doctors": results}

    def get_my_appointments(self) -> dict:
        with self._database.connect() as connection:
            upcoming = AppointmentRepository(connection).list_upcoming(
                self._patient.patient_id, self._now
            )
        return {
            "name": self._patient.name,
            "appointments": [self._appointment_summary(a) for a in upcoming],
        }

    def propose_booking(self, doctor_id: str, day: str, start: str) -> dict:
        doctor = self._doctor(doctor_id)
        starts_at = self._starts_at(day, start)
        self._propose(BookProposal(doctor_id=doctor.doctor_id, starts_at=starts_at))
        summary = {"doctor_id": doctor.doctor_id, "doctor": doctor.name}
        return awaiting("book") | summary | slot_ref(starts_at)

    def propose_cancellation(self, appointment_id: int) -> dict:
        self._propose(CancelProposal(appointment_id=appointment_id))
        return awaiting("cancel") | self._appointment_summary(
            self._find_appointment(appointment_id)
        )

    def propose_reschedule(self, appointment_id: int, day: str, start: str) -> dict:
        starts_at = self._starts_at(day, start)
        self._propose(
            RescheduleProposal(appointment_id=appointment_id, starts_at=starts_at)
        )
        current = self._appointment_summary(self._find_appointment(appointment_id))
        return awaiting("reschedule") | current | {"new": slot_ref(starts_at)}

    def _propose(self, proposal: Proposal) -> PendingAction:
        try:
            return self._actions.propose(
                self._conversation_id, self._patient.patient_id, proposal
            )
        except ActionConflictError as error:
            raise ToolError(str(error)) from error

    def _doctor(self, doctor_id: str) -> Doctor:
        doctor = self._clinic.find_doctor(doctor_id)
        if doctor is None:
            raise ToolError(f"Unknown doctor {doctor_id}; use list_doctors")
        return doctor

    def _starts_at(self, day: str, start: str) -> datetime:
        return datetime.combine(resolve_day(day, self._now.date()), parse_start(start))

    def _find_appointment(self, appointment_id: int) -> Appointment:
        with self._database.connect() as connection:
            return AppointmentRepository(connection).find(
                appointment_id, self._patient.patient_id
            )

    def _free_times(
        self,
        doctor: Doctor,
        day: date,
        booked: set[datetime],
        part_of_day: PartOfDay,
    ) -> list[str]:
        step = timedelta(minutes=self._clinic.slot_minutes)
        weekday = Weekday.of(day)
        times = []
        for block in sorted(doctor.schedule, key=lambda block: block.start):
            if block.day != weekday:
                continue
            moment = datetime.combine(day, block.start)
            end = datetime.combine(day, block.end)
            while moment + step <= end:
                if (
                    moment > self._now
                    and moment not in booked
                    and part_of_day.includes(moment.time())
                ):
                    times.append(f"{moment:%H:%M}")
                moment += step
        return times

    def _appointment_summary(self, appointment: Appointment) -> dict:
        doctor = self._clinic.find_doctor(appointment.doctor_id)
        return {
            "appointment_id": appointment.appointment_id,
            "doctor_id": doctor.doctor_id,
            "doctor": doctor.name,
            "specialty": doctor.specialty,
        } | slot_ref(appointment.starts_at)


def resolve_day(text: str, today: date) -> date:
    value = text.strip().casefold()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value in set(Weekday):
        offset = list(Weekday).index(Weekday(value)) - today.weekday()
        return today + timedelta(days=offset % 7)
    try:
        day = date.fromisoformat(value)
    except ValueError as error:
        raise ToolError(
            f"Unknown day {text!r}; use today, tomorrow, an English weekday name "
            "or YYYY-MM-DD"
        ) from error
    if day < today:
        raise ToolError(f"{day.isoformat()} is in the past")
    return day


def parse_specialty(value: str | None) -> Specialty | None:
    if value is None:
        return None
    try:
        return Specialty(value.strip().casefold())
    except ValueError as error:
        raise ToolError(f"Unknown specialty {value!r}; use {SPECIALTY_HELP}") from error


def parse_part_of_day(value: str) -> PartOfDay:
    try:
        return PartOfDay(value.strip().casefold())
    except ValueError as error:
        raise ToolError(
            f"Unknown part of day {value!r}; use morning, afternoon, evening or any"
        ) from error


def parse_start(value: str) -> time:
    try:
        start = time.fromisoformat(value.strip())
    except ValueError as error:
        raise ToolError(
            f"Unknown time {value!r}; use 24-hour HH:MM such as 14:30"
        ) from error
    return start.replace(tzinfo=None)


def doctor_summary(doctor: Doctor) -> dict:
    return {
        "doctor_id": doctor.doctor_id,
        "name": doctor.name,
        "specialty": doctor.specialty,
        "working_hours": [
            f"{block.day} {block.start:%H:%M}-{block.end:%H:%M}"
            for block in doctor.schedule
        ],
    }


def day_ref(day: date) -> dict:
    return {"date": day.isoformat(), "day": Weekday.of(day)}


def slot_ref(moment: datetime) -> dict:
    return day_ref(moment.date()) | {"time": f"{moment:%H:%M}"}


def awaiting(action: str) -> dict:
    return {
        "status": "awaiting_confirmation",
        "action": action,
        "note": "Nothing has changed yet. The patient must press Confirm.",
    }
