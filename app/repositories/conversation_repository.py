import sqlite3
from datetime import datetime
from typing import Literal
from uuid import uuid4

Role = Literal["user", "assistant", "system"]


class ConversationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, patient_id: str, created_at: datetime) -> str:
        conversation_id = str(uuid4())
        self._connection.execute(
            "INSERT INTO conversations (conversation_id, patient_id, created_at) "
            "VALUES (?, ?, ?)",
            (conversation_id, patient_id, created_at.isoformat(timespec="seconds")),
        )
        return conversation_id

    def belongs_to(self, conversation_id: str, patient_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ? AND patient_id = ?",
            (conversation_id, patient_id),
        ).fetchone()
        return row is not None

    def append(
        self, conversation_id: str, role: Role, content: str, created_at: datetime
    ) -> None:
        self._connection.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, created_at.isoformat(timespec="seconds")),
        )

    def recent(self, conversation_id: str, max_turns: int) -> list[dict]:
        rows = self._connection.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? AND message_id >= ("
            "SELECT MIN(message_id) FROM ("
            "SELECT message_id FROM messages "
            "WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY message_id DESC LIMIT ?)) "
            "ORDER BY message_id",
            (conversation_id, conversation_id, max_turns),
        ).fetchall()
        return [dict(row) for row in rows]
