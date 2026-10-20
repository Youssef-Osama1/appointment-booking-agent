from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.config.settings import get_settings
from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
    AgentStepLimitError,
    ConversationNotFoundError,
    LLMOutputError,
    PatientNotFoundError,
)
from app.core.logger import setup_logging
from app.repositories.appointment_repository import initialize_database
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.services.llm_client import LLMClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    app.state.clinic = ClinicRepository.from_file(settings.clinic_path)
    app.state.database = Database(settings.database_path)
    initialize_database(app.state.database, app.state.clinic.seed_appointments)
    app.state.llm_client = LLMClient(settings)
    yield
    await app.state.llm_client.close()


app = FastAPI(title="Appointment Booking Agent", lifespan=lifespan)
app.include_router(api_router)


@app.exception_handler(LLMOutputError)
@app.exception_handler(AgentStepLimitError)
async def handle_model_failure(
    request: Request, error: LLMOutputError | AgentStepLimitError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(error)}
    )


@app.exception_handler(PatientNotFoundError)
@app.exception_handler(ConversationNotFoundError)
@app.exception_handler(ActionNotFoundError)
async def handle_not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(error)}
    )


@app.exception_handler(ActionConflictError)
async def handle_conflict(request: Request, error: ActionConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT, content={"detail": str(error)}
    )


@app.exception_handler(ActionExpiredError)
async def handle_expired(request: Request, error: ActionExpiredError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_410_GONE, content={"detail": str(error)}
    )
