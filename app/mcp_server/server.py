from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from app.config.settings import Settings
from app.core.exceptions import ToolError
from app.prompts.booking import (
    FIND_SLOTS_DESCRIPTION,
    GET_MY_APPOINTMENTS_DESCRIPTION,
    LIST_DOCTORS_DESCRIPTION,
)
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
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


def build_server(
    clinic: ClinicRepository,
    database: Database,
    settings: Settings,
    patient: Patient,
    conversation_id: str,
) -> MCPServer:
    server = MCPServer(name="appointment-booking")

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

    return server
