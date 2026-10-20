import logging
import sqlite3
from datetime import datetime, timedelta

from app.config.settings import Settings
from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.schemas.actions import (
    ActionStatus,
    BookProposal,
    CancelProposal,
    PendingAction,
    Proposal,
    RescheduleProposal,
)
from app.schemas.clinic import Appointment, AppointmentStatus
from app.services.booking_rules import BookingRules

logger = logging.getLogger(__name__)


class ActionService:
    def __init__(
        self, database: Database, clinic: ClinicRepository, settings: Settings
    ) -> None:
        self._database = database
        self._clinic = clinic
        self._settings = settings

    def propose(
        self, conversation_id: str, patient_id: str, proposal: Proposal
    ) -> PendingAction:
        now = self._settings.current_time()
        expires_at = now + timedelta(minutes=self._settings.pending_action_ttl_minutes)
        with self._database.transaction() as connection:
            check(proposal, self._rules(connection, patient_id, now))
            actions = ActionRepository(connection)
            replaced = actions.supersede(conversation_id)
            action = actions.create(
                conversation_id, patient_id, proposal, now, expires_at
            )
        logger.info(
            "Proposed action: action_id=%s kind=%s replaced=%s",
            action.action_id,
            proposal.kind,
            replaced,
        )
        return action

    def confirm(self, action_id: str, patient_id: str) -> PendingAction:
        now = self._settings.current_time()
        with self._database.transaction() as connection:
            actions = ActionRepository(connection)
            action = actions.find(action_id, patient_id)
            if action is None:
                raise ActionNotFoundError(f"Action {action_id} was not found")
            if action.status is ActionStatus.CONFIRMED:
                logger.info("Action already confirmed: action_id=%s", action_id)
                return action
            if action.status is ActionStatus.SUPERSEDED:
                raise ActionExpiredError("This proposal was replaced by a newer one")
            if action.is_expired(now):
                raise ActionExpiredError("This proposal has expired")

            rules = self._rules(connection, patient_id, now)
            result = execute(
                action.proposal, rules, AppointmentRepository(connection), patient_id
            )
            actions.mark_confirmed(action_id, result)
            ConversationRepository(connection).append(
                action.conversation_id,
                "system",
                confirmation_note(action.proposal, result, self._clinic),
                now,
            )
        logger.info(
            "Confirmed action: action_id=%s kind=%s appointment_id=%s",
            action_id,
            action.proposal.kind,
            result.appointment_id,
        )
        return action.model_copy(
            update={"status": ActionStatus.CONFIRMED, "result": result}
        )

    def _rules(
        self, connection: sqlite3.Connection, patient_id: str, now: datetime
    ) -> BookingRules:
        return BookingRules(
            self._clinic, AppointmentRepository(connection), patient_id, now
        )


def check(proposal: Proposal, rules: BookingRules) -> None:
    match proposal:
        case BookProposal():
            rules.check_booking(proposal.doctor_id, proposal.starts_at)
        case CancelProposal():
            rules.check_cancellation(proposal.appointment_id)
        case RescheduleProposal():
            rules.check_reschedule(proposal.appointment_id, proposal.starts_at)


def execute(
    proposal: Proposal,
    rules: BookingRules,
    appointments: AppointmentRepository,
    patient_id: str,
) -> Appointment:
    try:
        match proposal:
            case BookProposal():
                rules.check_booking(proposal.doctor_id, proposal.starts_at)
                return appointments.create(
                    patient_id, proposal.doctor_id, proposal.starts_at
                )
            case CancelProposal():
                appointment = rules.check_cancellation(proposal.appointment_id)
                appointments.cancel(proposal.appointment_id, patient_id)
                return appointment.model_copy(
                    update={"status": AppointmentStatus.CANCELLED}
                )
            case RescheduleProposal():
                appointment = rules.check_reschedule(
                    proposal.appointment_id, proposal.starts_at
                )
                appointments.reschedule(
                    proposal.appointment_id, patient_id, proposal.starts_at
                )
                return appointment.model_copy(update={"starts_at": proposal.starts_at})
    except sqlite3.IntegrityError as error:
        raise ActionConflictError("That time is no longer available") from error


def confirmation_note(
    proposal: Proposal, result: Appointment, clinic: ClinicRepository
) -> str:
    doctor = clinic.find_doctor(result.doctor_id).name
    when = f"{result.starts_at:%A %Y-%m-%d at %H:%M}"
    number = f"appointment {result.appointment_id}"
    match proposal:
        case BookProposal():
            return f"The patient confirmed: booked {number} with {doctor} on {when}."
        case CancelProposal():
            return f"The patient confirmed: cancelled {number} with {doctor} on {when}."
        case RescheduleProposal():
            return f"The patient confirmed: moved {number} with {doctor} to {when}."
