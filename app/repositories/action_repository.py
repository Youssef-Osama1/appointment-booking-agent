import json
import sqlite3
from datetime import datetime
from uuid import uuid4

from app.schemas.actions import ActionStatus, PendingAction, Proposal
from app.schemas.clinic import Appointment

COLUMNS = (
    "action_id, conversation_id, patient_id, proposal, status, result, "
    "created_at, expires_at"
)
PENDING = ActionStatus.PENDING


class ActionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        conversation_id: str,
        patient_id: str,
        proposal: Proposal,
        created_at: datetime,
        expires_at: datetime,
    ) -> PendingAction:
        action = PendingAction(
            action_id=str(uuid4()),
            conversation_id=conversation_id,
            patient_id=patient_id,
            proposal=proposal,
            status=PENDING,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._connection.execute(
            "INSERT INTO pending_actions "
            "(action_id, conversation_id, patient_id, kind, proposal, status, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.action_id,
                conversation_id,
                patient_id,
                proposal.kind,
                proposal.model_dump_json(),
                PENDING,
                action_time(created_at),
                action_time(expires_at),
            ),
        )
        return action

    def supersede(self, conversation_id: str) -> int:
        cursor = self._connection.execute(
            "UPDATE pending_actions SET status = ? "
            "WHERE conversation_id = ? AND status = ?",
            (ActionStatus.SUPERSEDED, conversation_id, PENDING),
        )
        return cursor.rowcount

    def find(self, action_id: str, patient_id: str) -> PendingAction | None:
        row = self._connection.execute(
            f"SELECT {COLUMNS} FROM pending_actions "
            "WHERE action_id = ? AND patient_id = ?",
            (action_id, patient_id),
        ).fetchone()
        return to_action(row) if row else None

    def find_pending(self, conversation_id: str) -> PendingAction | None:
        row = self._connection.execute(
            f"SELECT {COLUMNS} FROM pending_actions "
            "WHERE conversation_id = ? AND status = ?",
            (conversation_id, PENDING),
        ).fetchone()
        return to_action(row) if row else None

    def mark_confirmed(self, action_id: str, result: Appointment) -> bool:
        cursor = self._connection.execute(
            "UPDATE pending_actions SET status = ?, result = ? "
            "WHERE action_id = ? AND status = ?",
            (ActionStatus.CONFIRMED, result.model_dump_json(), action_id, PENDING),
        )
        return cursor.rowcount == 1


def action_time(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def to_action(row: sqlite3.Row) -> PendingAction:
    action = dict(row)
    action["proposal"] = json.loads(action["proposal"])
    if action["result"] is not None:
        action["result"] = json.loads(action["result"])
    return PendingAction.model_validate(action)
