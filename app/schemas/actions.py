from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.clinic import Appointment


class ActionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class BookProposal(BaseModel):
    kind: Literal["book"] = "book"
    doctor_id: str
    starts_at: datetime


class CancelProposal(BaseModel):
    kind: Literal["cancel"] = "cancel"
    appointment_id: int


class RescheduleProposal(BaseModel):
    kind: Literal["reschedule"] = "reschedule"
    appointment_id: int
    starts_at: datetime


Proposal = Annotated[
    BookProposal | CancelProposal | RescheduleProposal, Field(discriminator="kind")
]


class PendingAction(BaseModel):
    action_id: str
    conversation_id: str
    patient_id: str
    proposal: Proposal
    status: ActionStatus
    result: Appointment | None = None
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return self.status is ActionStatus.PENDING and now >= self.expires_at
