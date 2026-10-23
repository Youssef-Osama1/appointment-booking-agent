from app.config.settings import Settings
from app.repositories.appointment_repository import initialize_database
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.database import Database


def open_storage(settings: Settings) -> tuple[ClinicRepository, Database]:
    clinic = ClinicRepository.from_file(settings.clinic_path)
    database = Database(settings.database_path)
    initialize_database(database, clinic.seed_appointments)
    return clinic, database
