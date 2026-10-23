import sys

from app.config.settings import get_settings
from app.core.logger import setup_logging
from app.mcp_server.approval import confirm_in_terminal
from app.mcp_server.server import build_server
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.storage import open_storage


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    clinic, database = open_storage(settings)
    patient = clinic.find_patient(settings.mcp_patient_id or "")
    if patient is None:
        raise SystemExit(
            f"Set MCP_PATIENT_ID to a known patient, got {settings.mcp_patient_id!r}"
        )
    if sys.argv[1:] == ["confirm"]:
        raise SystemExit(confirm_in_terminal(clinic, database, settings, patient))
    with database.transaction() as connection:
        conversation_id = ConversationRepository(connection).create(
            patient.patient_id, settings.current_time()
        )
    build_server(clinic, database, settings, patient, conversation_id).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
