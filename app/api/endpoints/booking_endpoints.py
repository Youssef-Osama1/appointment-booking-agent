from fastapi import APIRouter

from app.api.dependencies import ActionServiceDep, BookingServiceDep
from app.api.schemas.booking import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConfirmResponse,
)

router = APIRouter()


@router.post("/chat")
async def chat(payload: ChatRequest, service: BookingServiceDep) -> ChatResponse:
    result = await service.chat(
        payload.patient_id, payload.message, payload.conversation_id
    )
    return ChatResponse.model_validate(result, from_attributes=True)


@router.post("/actions/{action_id}/confirm")
def confirm(
    action_id: str, payload: ConfirmRequest, service: ActionServiceDep
) -> ConfirmResponse:
    action = service.confirm(action_id, payload.patient_id)
    return ConfirmResponse(
        action_id=action.action_id, status=action.status, appointment=action.result
    )
