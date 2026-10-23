from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from app.config.settings import Settings
from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
    ToolError,
)
from app.prompts.booking import (
    FIND_SLOTS_DESCRIPTION,
    GET_MY_APPOINTMENTS_DESCRIPTION,
    LIST_DOCTORS_DESCRIPTION,
    PROPOSE_BOOKING_DESCRIPTION,
    PROPOSE_CANCELLATION_DESCRIPTION,
    PROPOSE_RESCHEDULE_DESCRIPTION,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.schemas.actions import (
    BookProposal,
    CancelProposal,
    PendingAction,
)
from app.schemas.clinic import Patient
from app.services.action_service import ActionService
from app.tools.booking import (
    DAY_HELP,
    SPECIALTY_HELP,
    DoctorId,
    PatientTools,
    ShortText,
)

READ_ONLY = ToolAnnotations(read_only_hint=True)
INSTRUCTIONS = (
    "After a propose tool succeeds, call request_confirmation. The patient approves "
    "or rejects the proposal in a form you cannot answer for them."
)
REQUEST_CONFIRMATION_DESCRIPTION = (
    "Ask the patient to approve the waiting proposal. The patient answers in a "
    "form; only their approval books, cancels or moves anything."
)


class Approval(BaseModel):
    pass


def build_server(
    clinic: ClinicRepository,
    database: Database,
    settings: Settings,
    patient: Patient,
    conversation_id: str,
) -> MCPServer:
    server = MCPServer(name="appointment-booking", instructions=INSTRUCTIONS)

    def run[T](call: Callable[[PatientTools], T]) -> T:
        tools = PatientTools(
            clinic,
            database,
            ActionService(database, clinic, settings),
            patient,
            conversation_id,
            settings.current_time(),
        )
        try:
            return call(tools)
        except ToolError as error:
            raise MCPToolError(str(error)) from error

    @server.tool(description=LIST_DOCTORS_DESCRIPTION, annotations=READ_ONLY)
    def list_doctors(
        specialty: Annotated[
            ShortText | None,
            Field(description=f"{SPECIALTY_HELP}, or omit for all doctors"),
        ] = None,
    ) -> dict:
        return run(lambda tools: tools.list_doctors(specialty))

    @server.tool(description=FIND_SLOTS_DESCRIPTION, annotations=READ_ONLY)
    def find_slots(
        day: Annotated[ShortText, Field(description=DAY_HELP)],
        specialty: Annotated[
            ShortText | None,
            Field(description=f"Search every doctor of: {SPECIALTY_HELP}"),
        ] = None,
        doctor_id: Annotated[
            DoctorId | None, Field(description="Search one doctor, e.g. D01")
        ] = None,
        part_of_day: Annotated[
            ShortText,
            Field(
                description="morning (before 12:00), afternoon (12:00-17:00), "
                "evening or any"
            ),
        ] = "any",
    ) -> dict:
        return run(
            lambda tools: tools.find_slots(day, specialty, doctor_id, part_of_day)
        )

    @server.tool(description=GET_MY_APPOINTMENTS_DESCRIPTION, annotations=READ_ONLY)
    def get_my_appointments() -> dict:
        return run(lambda tools: tools.get_my_appointments())

    @server.tool(description=PROPOSE_BOOKING_DESCRIPTION)
    def propose_booking(
        doctor_id: Annotated[
            DoctorId, Field(description="Doctor ID from the tools, e.g. D01")
        ],
        day: Annotated[ShortText, Field(description=DAY_HELP)],
        start: Annotated[
            ShortText, Field(description="Start time in 24-hour HH:MM, e.g. 10:00")
        ],
    ) -> dict:
        return run(lambda tools: tools.propose_booking(doctor_id, day, start))

    @server.tool(description=PROPOSE_CANCELLATION_DESCRIPTION)
    def propose_cancellation(
        appointment_id: Annotated[
            int, Field(description="Appointment ID from get_my_appointments")
        ],
    ) -> dict:
        return run(lambda tools: tools.propose_cancellation(appointment_id))

    @server.tool(description=PROPOSE_RESCHEDULE_DESCRIPTION)
    def propose_reschedule(
        appointment_id: Annotated[
            int, Field(description="Appointment ID from get_my_appointments")
        ],
        day: Annotated[ShortText, Field(description=f"New day: {DAY_HELP}")],
        start: Annotated[
            ShortText, Field(description="New start time in 24-hour HH:MM, e.g. 10:00")
        ],
    ) -> dict:
        return run(lambda tools: tools.propose_reschedule(appointment_id, day, start))

    def waiting_action() -> PendingAction:
        with database.connect() as connection:
            action = ActionRepository(connection).find_pending(conversation_id)
        if action is None or action.is_expired(settings.current_time()):
            raise MCPToolError(
                "Nothing is waiting for approval; call a propose tool first"
            )
        return action

    def describe(action: PendingAction) -> str:
        proposal = action.proposal
        if isinstance(proposal, BookProposal):
            doctor = clinic.find_doctor(proposal.doctor_id).name
            return f"Book {doctor} on {when(proposal.starts_at)}?"
        with database.connect() as connection:
            current = AppointmentRepository(connection).find(
                proposal.appointment_id, patient.patient_id
            )
        doctor = clinic.find_doctor(current.doctor_id).name
        if isinstance(proposal, CancelProposal):
            return (
                f"Cancel your appointment with {doctor} on {when(current.starts_at)}?"
            )
        return (
            f"Move your appointment with {doctor} from {when(current.starts_at)} "
            f"to {when(proposal.starts_at)}?"
        )

    def ask_patient(
        action: Annotated[PendingAction, Resolve(waiting_action)],
    ) -> Elicit[Approval]:
        return Elicit(describe(action), Approval)

    @server.tool(description=REQUEST_CONFIRMATION_DESCRIPTION)
    def request_confirmation(
        action: Annotated[PendingAction, Resolve(waiting_action)],
        answer: Annotated[ElicitationResult[Approval], Resolve(ask_patient)],
    ) -> dict:
        if not isinstance(answer, AcceptedElicitation):
            return {"status": "not_approved", "note": "Nothing changed."}
        try:
            confirmed = ActionService(database, clinic, settings).confirm(
                action.action_id, patient.patient_id
            )
        except (ActionNotFoundError, ActionExpiredError, ActionConflictError) as error:
            raise MCPToolError(str(error)) from error
        return {
            "status": "confirmed",
            "appointment": confirmed.result.model_dump(mode="json"),
        }

    return server


def when(moment: datetime) -> str:
    return f"{moment:%A %Y-%m-%d at %H:%M}"
