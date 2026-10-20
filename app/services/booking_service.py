import logging
from datetime import datetime

from pydantic import BaseModel

from app.config.settings import Settings
from app.core.exceptions import ConversationNotFoundError, PatientNotFoundError
from app.prompts.booking import (
    FIND_SLOTS_DESCRIPTION,
    GET_MY_APPOINTMENTS_DESCRIPTION,
    LIST_DOCTORS_DESCRIPTION,
    PROPOSE_BOOKING_DESCRIPTION,
    PROPOSE_CANCELLATION_DESCRIPTION,
    PROPOSE_RESCHEDULE_DESCRIPTION,
    booking_system_prompt,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.schemas.actions import PendingAction
from app.services.action_service import ActionService
from app.services.agent import Tool, ToolCallRecord, ToolRegistry, run_agent
from app.services.llm_client import LLMClient
from app.tools.booking import (
    FindSlotsArgs,
    GetMyAppointmentsArgs,
    ListDoctorsArgs,
    PatientTools,
    ProposeBookingArgs,
    ProposeCancellationArgs,
    ProposeRescheduleArgs,
)

logger = logging.getLogger(__name__)


class ChatResult(BaseModel):
    conversation_id: str
    reply: str
    pending_action: PendingAction | None
    tool_calls: list[ToolCallRecord]
    steps: int


class BookingService:
    def __init__(
        self,
        llm_client: LLMClient,
        database: Database,
        clinic: ClinicRepository,
        settings: Settings,
    ) -> None:
        self._llm_client = llm_client
        self._database = database
        self._clinic = clinic
        self._settings = settings

    async def chat(
        self, patient_id: str, message: str, conversation_id: str | None = None
    ) -> ChatResult:
        patient = self._clinic.find_patient(patient_id)
        if patient is None:
            raise PatientNotFoundError(f"Patient {patient_id} was not found")
        now = self._settings.current_time()
        conversation_id, history = self._open(patient_id, conversation_id, now)

        tools = PatientTools(
            self._clinic,
            self._database,
            ActionService(self._database, self._clinic, self._settings),
            patient,
            conversation_id,
            now,
        )
        result = await run_agent(
            llm_client=self._llm_client,
            registry=build_registry(tools),
            system=booking_system_prompt(now),
            message=message,
            max_steps=self._settings.agent_max_steps,
            history=history,
        )

        with self._database.transaction() as connection:
            conversations = ConversationRepository(connection)
            conversations.append(conversation_id, "user", message, now)
            conversations.append(conversation_id, "assistant", result.reply, now)
            pending = ActionRepository(connection).find_pending(conversation_id)
        if pending is not None and pending.is_expired(now):
            pending = None

        logger.info(
            "Answered booking message: steps=%s tool_calls=%s failed_calls=%s "
            "pending=%s",
            result.steps,
            len(result.tool_calls),
            sum(not call.ok for call in result.tool_calls),
            pending.proposal.kind if pending else None,
        )
        return ChatResult(
            conversation_id=conversation_id,
            reply=result.reply,
            pending_action=pending,
            tool_calls=result.tool_calls,
            steps=result.steps,
        )

    def _open(
        self, patient_id: str, conversation_id: str | None, now: datetime
    ) -> tuple[str, list[dict]]:
        with self._database.transaction() as connection:
            conversations = ConversationRepository(connection)
            if conversation_id is None:
                return conversations.create(patient_id, now), []
            if not conversations.belongs_to(conversation_id, patient_id):
                raise ConversationNotFoundError(
                    f"Conversation {conversation_id} was not found"
                )
            history = conversations.recent(
                conversation_id, self._settings.history_max_turns
            )
        return conversation_id, history


def build_registry(tools: PatientTools) -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                "list_doctors",
                LIST_DOCTORS_DESCRIPTION,
                ListDoctorsArgs,
                tools.list_doctors,
            ),
            Tool("find_slots", FIND_SLOTS_DESCRIPTION, FindSlotsArgs, tools.find_slots),
            Tool(
                "get_my_appointments",
                GET_MY_APPOINTMENTS_DESCRIPTION,
                GetMyAppointmentsArgs,
                tools.get_my_appointments,
            ),
            Tool(
                "propose_booking",
                PROPOSE_BOOKING_DESCRIPTION,
                ProposeBookingArgs,
                tools.propose_booking,
            ),
            Tool(
                "propose_cancellation",
                PROPOSE_CANCELLATION_DESCRIPTION,
                ProposeCancellationArgs,
                tools.propose_cancellation,
            ),
            Tool(
                "propose_reschedule",
                PROPOSE_RESCHEDULE_DESCRIPTION,
                ProposeRescheduleArgs,
                tools.propose_reschedule,
            ),
        ]
    )
