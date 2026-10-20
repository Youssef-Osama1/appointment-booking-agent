import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id INTEGER PRIMARY KEY,
    patient_id TEXT NOT NULL,
    doctor_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_doctor_slot
    ON appointments (doctor_id, starts_at) WHERE status = 'booked';

CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_patient_slot
    ON appointments (patient_id, starts_at) WHERE status = 'booked';
"""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path

    def create_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
