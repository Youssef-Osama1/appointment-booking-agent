from typing import Annotated

from fastapi import Depends, Request

from app.config.settings import Settings, get_settings
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.services.action_service import ActionService
from app.services.booking_service import BookingService
from app.services.llm_client import LLMClient


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_clinic(request: Request) -> ClinicRepository:
    return request.app.state.clinic


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
DatabaseDep = Annotated[Database, Depends(get_database)]
ClinicDep = Annotated[ClinicRepository, Depends(get_clinic)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_booking_service(
    llm_client: LLMClientDep,
    database: DatabaseDep,
    clinic: ClinicDep,
    settings: SettingsDep,
) -> BookingService:
    return BookingService(llm_client, database, clinic, settings)


def get_action_service(
    database: DatabaseDep, clinic: ClinicDep, settings: SettingsDep
) -> ActionService:
    return ActionService(database, clinic, settings)


BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]
ActionServiceDep = Annotated[ActionService, Depends(get_action_service)]
