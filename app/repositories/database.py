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

CREATE TABLE IF NOT EXISTS pending_actions (
    action_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('book', 'cancel', 'reschedule')),
    proposal TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'superseded')),
    result TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_pending_action_per_conversation
    ON pending_actions (conversation_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (conversation_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS messages_by_conversation
    ON messages (conversation_id, message_id);
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
