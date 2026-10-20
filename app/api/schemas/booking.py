from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.schemas.actions import ActionStatus, Proposal
from app.schemas.clinic import Appointment
from app.services.agent import ToolCallRecord

PatientId = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[Pp]\d{4}$")
]
PatientMessage = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
]
ConversationId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class ChatRequest(BaseModel):
    patient_id: PatientId
    message: PatientMessage
    conversation_id: ConversationId | None = None


class ConfirmRequest(BaseModel):
    patient_id: PatientId


class PendingActionView(BaseModel):
    action_id: str
    proposal: Proposal
    expires_at: datetime


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    pending_action: PendingActionView | None
    tool_calls: list[ToolCallRecord]
    steps: int


class ConfirmResponse(BaseModel):
    action_id: str
    status: ActionStatus
    appointment: Appointment
