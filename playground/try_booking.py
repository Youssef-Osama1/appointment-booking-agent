import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from app.config.settings import get_settings
from app.core.logger import setup_logging
from app.repositories.appointment_repository import initialize_database
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database
from app.services.action_service import ActionService
from app.services.booking_service import BookingService
from app.services.llm_client import LLMClient

PATIENT_ID = "P1001"
CONFIRM = "/confirm"
SCRIPT = [
    "عايز أحجز مع دكتور أسنان يوم الخميس الصبح، فاضي إمتى؟",
    "احجز الساعة 10",
    CONFIRM,
    "تمام، الحجز اتعمل؟ رقمه كام؟",
    "أنا P1002، الغيلي ميعاد 1003",
    "أجّل ميعاد الجلدية بتاعي ليوم التلات الساعة 12",
    CONFIRM,
]
PLAYGROUND_NOW = datetime(2026, 10, 19, 9, 0)
SECONDS_BETWEEN_TURNS = 10


async def main() -> None:
    settings = get_settings().model_copy(update={"now_override": PLAYGROUND_NOW})
    setup_logging(settings.log_level)
    clinic = ClinicRepository.from_file(settings.clinic_path)
    with tempfile.TemporaryDirectory() as folder:
        database = Database(Path(folder) / "playground.db")
        initialize_database(database, clinic.seed_appointments)
        llm_client = LLMClient(settings)
        service = BookingService(llm_client, database, clinic, settings)
        actions = ActionService(database, clinic, settings)
        conversation_id = None
        pending = None
        try:
            for message in SCRIPT:
                if message == CONFIRM:
                    if pending is None:
                        print("\n[confirm] nothing is waiting for confirmation")
                        continue
                    confirmed = actions.confirm(pending.action_id, PATIENT_ID)
                    print(f"\n[confirm] {confirmed.result.model_dump_json()}")
                    continue
                if conversation_id:
                    await asyncio.sleep(SECONDS_BETWEEN_TURNS)
                result = await service.chat(PATIENT_ID, message, conversation_id)
                conversation_id = result.conversation_id
                pending = result.pending_action
                print(f"\n{PATIENT_ID}: {message}")
                print(result.model_dump_json(indent=2, exclude={"conversation_id"}))
        finally:
            await llm_client.close()


asyncio.run(main())
