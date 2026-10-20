import sqlite3
from datetime import date, datetime, time, timedelta

from app.repositories.database import Database
from app.schemas.clinic import Appointment, AppointmentStatus

COLUMNS = "appointment_id, patient_id, doctor_id, starts_at, status"
BOOKED = AppointmentStatus.BOOKED


class AppointmentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def count(self) -> int:
        query = "SELECT COUNT(*) FROM appointments"
        return self._connection.execute(query).fetchone()[0]

    def seed(self, appointments: list[Appointment]) -> None:
        self._connection.executemany(
            f"INSERT INTO appointments ({COLUMNS}) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    appointment.appointment_id,
                    appointment.patient_id,
                    appointment.doctor_id,
                    db_time(appointment.starts_at),
                    appointment.status,
                )
                for appointment in appointments
            ],
        )

    def find(self, appointment_id: int, patient_id: str) -> Appointment | None:
        row = self._connection.execute(
            f"SELECT {COLUMNS} FROM appointments "
            "WHERE appointment_id = ? AND patient_id = ?",
            (appointment_id, patient_id),
        ).fetchone()
        return to_appointment(row) if row else None

    def list_upcoming(self, patient_id: str, now: datetime) -> list[Appointment]:
        rows = self._connection.execute(
            f"SELECT {COLUMNS} FROM appointments "
            "WHERE patient_id = ? AND status = ? AND starts_at >= ? "
            "ORDER BY starts_at",
            (patient_id, BOOKED, db_time(now)),
        ).fetchall()
        return [to_appointment(row) for row in rows]

    def booked_times(self, doctor_id: str, day: date) -> set[datetime]:
        start = datetime.combine(day, time())
        rows = self._connection.execute(
            "SELECT starts_at FROM appointments "
            "WHERE doctor_id = ? AND status = ? AND starts_at >= ? AND starts_at < ?",
            (doctor_id, BOOKED, db_time(start), db_time(start + timedelta(days=1))),
        ).fetchall()
        return {datetime.fromisoformat(row["starts_at"]) for row in rows}

    def is_doctor_booked(self, doctor_id: str, starts_at: datetime) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM appointments "
            "WHERE doctor_id = ? AND starts_at = ? AND status = ?",
            (doctor_id, db_time(starts_at), BOOKED),
        ).fetchone()
        return row is not None

    def is_patient_booked(self, patient_id: str, starts_at: datetime) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM appointments "
            "WHERE patient_id = ? AND starts_at = ? AND status = ?",
            (patient_id, db_time(starts_at), BOOKED),
        ).fetchone()
        return row is not None

    def create(
        self, patient_id: str, doctor_id: str, starts_at: datetime
    ) -> Appointment:
        cursor = self._connection.execute(
            "INSERT INTO appointments (patient_id, doctor_id, starts_at, status) "
            "VALUES (?, ?, ?, ?)",
            (patient_id, doctor_id, db_time(starts_at), BOOKED),
        )
        return Appointment(
            appointment_id=cursor.lastrowid,
            patient_id=patient_id,
            doctor_id=doctor_id,
            starts_at=starts_at,
        )

    def cancel(self, appointment_id: int, patient_id: str) -> bool:
        cursor = self._connection.execute(
            "UPDATE appointments SET status = ? "
            "WHERE appointment_id = ? AND patient_id = ? AND status = ?",
            (AppointmentStatus.CANCELLED, appointment_id, patient_id, BOOKED),
        )
        return cursor.rowcount == 1

    def reschedule(
        self, appointment_id: int, patient_id: str, starts_at: datetime
    ) -> bool:
        cursor = self._connection.execute(
            "UPDATE appointments SET starts_at = ? "
            "WHERE appointment_id = ? AND patient_id = ? AND status = ?",
            (db_time(starts_at), appointment_id, patient_id, BOOKED),
        )
        return cursor.rowcount == 1


def initialize_database(database: Database, appointments: list[Appointment]) -> None:
    database.create_schema()
    with database.transaction() as connection:
        repository = AppointmentRepository(connection)
        if repository.count() == 0:
            repository.seed(appointments)


def db_time(moment: datetime) -> str:
    return moment.isoformat(timespec="minutes")


def to_appointment(row: sqlite3.Row) -> Appointment:
    return Appointment.model_validate(dict(row))
