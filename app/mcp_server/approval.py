import sys
from datetime import datetime

from app.config.settings import Settings
from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.schemas.actions import BookProposal, CancelProposal, PendingAction
from app.schemas.clinic import Patient
from app.services.action_service import ActionService

CONFIRM_COMMAND = "uv run python -m app.mcp_server confirm"
APPROVAL_ERRORS = (ActionNotFoundError, ActionExpiredError, ActionConflictError)


def describe(
    action: PendingAction, clinic: ClinicRepository, database: Database
) -> str:
    proposal = action.proposal
    if isinstance(proposal, BookProposal):
        doctor = clinic.find_doctor(proposal.doctor_id).name
        return f"Book {doctor} on {when(proposal.starts_at)}?"
    with database.connect() as connection:
        current = AppointmentRepository(connection).find(
            proposal.appointment_id, action.patient_id
        )
    doctor = clinic.find_doctor(current.doctor_id).name
    if isinstance(proposal, CancelProposal):
        return f"Cancel your appointment with {doctor} on {when(current.starts_at)}?"
    return (
        f"Move your appointment with {doctor} from {when(current.starts_at)} "
        f"to {when(proposal.starts_at)}?"
    )


def confirm_in_terminal(
    clinic: ClinicRepository, database: Database, settings: Settings, patient: Patient
) -> int:
    if not sys.stdin.isatty():
        print("Run this command yourself in a terminal.")
        return 1
    with database.connect() as connection:
        action = ActionRepository(connection).find_latest_pending(patient.patient_id)
    if action is None or action.is_expired(settings.current_time()):
        print("Nothing is waiting for approval.")
        return 1
    answer = input(f"{describe(action, clinic, database)} [y/N] ")
    if answer.strip().casefold() != "y":
        print("Nothing changed.")
        return 0
    try:
        confirmed = ActionService(database, clinic, settings).confirm(
            action.action_id, patient.patient_id
        )
    except APPROVAL_ERRORS as error:
        print(error)
        return 1
    print(f"Done. Appointment {confirmed.result.appointment_id} is updated.")
    return 0


def when(moment: datetime) -> str:
    return f"{moment:%A %Y-%m-%d at %H:%M}"
