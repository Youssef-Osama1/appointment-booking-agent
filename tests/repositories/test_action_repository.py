import sqlite3
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from app.repositories.action_repository import ActionRepository, action_time
from app.repositories.database import Database
from app.schemas.actions import (
    ActionStatus,
    BookProposal,
    CancelProposal,
    RescheduleProposal,
)
from app.schemas.clinic import Appointment, AppointmentStatus

NOW = datetime(2026, 10, 19, 9, 0, 45)
EXPIRES = NOW + timedelta(minutes=10)
BOOK = BookProposal(doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 0))
CANCEL = CancelProposal(appointment_id=1002)
RESCHEDULE = RescheduleProposal(
    appointment_id=1002, starts_at=datetime(2026, 10, 27, 12, 0)
)
BOOKED = Appointment(
    appointment_id=1008,
    patient_id="P1001",
    doctor_id="D01",
    starts_at=datetime(2026, 10, 22, 10, 0),
)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "booking.db")
    database.create_schema()
    return database


@pytest.fixture
def repository(database):
    with database.connect() as connection:
        yield ActionRepository(connection)


def create(repository, proposal=BOOK, conversation_id="c1", patient_id="P1001"):
    return repository.create(conversation_id, patient_id, proposal, NOW, EXPIRES)


def raw_rows(database):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT kind, status, proposal, result, created_at, expires_at "
            "FROM pending_actions ORDER BY rowid"
        )
        return [dict(row) for row in rows]


def test_action_time_keeps_seconds():
    assert action_time(NOW) == "2026-10-19T09:00:45"
    assert action_time(NOW.replace(microsecond=999)) == "2026-10-19T09:00:45"


def test_create_returns_a_pending_action(repository):
    action = create(repository)

    assert UUID(action.action_id).version == 4
    assert action.conversation_id == "c1"
    assert action.patient_id == "P1001"
    assert action.proposal == BOOK
    assert action.status is ActionStatus.PENDING
    assert action.result is None
    assert action.created_at == NOW
    assert action.expires_at == EXPIRES


def test_create_gives_every_action_a_new_id(repository):
    first = create(repository, conversation_id="c1")
    second = create(repository, conversation_id="c2")

    assert first.action_id != second.action_id


def test_create_stores_kind_json_and_seconds(database, repository):
    create(repository)

    row = raw_rows(database)[0]
    assert row["kind"] == "book"
    assert row["status"] == "pending"
    assert row["result"] is None
    assert row["created_at"] == "2026-10-19T09:00:45"
    assert row["expires_at"] == "2026-10-19T09:10:45"
    assert BookProposal.model_validate_json(row["proposal"]) == BOOK


@pytest.mark.parametrize("proposal", [BOOK, CANCEL, RESCHEDULE])
def test_find_reads_back_every_proposal_kind(repository, proposal):
    action = create(repository, proposal)

    found = repository.find(action.action_id, "P1001")

    assert found == action
    assert type(found.proposal) is type(proposal)


@pytest.mark.parametrize(
    ("action_id", "patient_id"),
    [(None, "P1002"), ("missing", "P1001"), (None, "")],
)
def test_find_hides_other_patients_and_unknown_ids(repository, action_id, patient_id):
    action = create(repository)

    assert repository.find(action_id or action.action_id, patient_id) is None


def test_one_conversation_cannot_hold_two_pending_actions(repository):
    create(repository)

    with pytest.raises(sqlite3.IntegrityError, match="conversation_id"):
        create(repository, CANCEL)


def test_different_conversations_can_each_hold_a_pending_action(repository):
    create(repository, conversation_id="c1")
    create(repository, conversation_id="c2")

    assert repository.find_pending("c1").conversation_id == "c1"
    assert repository.find_pending("c2").conversation_id == "c2"


def test_supersede_frees_the_conversation_for_a_new_proposal(repository):
    first = create(repository)

    assert repository.supersede("c1") == 1
    second = create(repository, CANCEL)

    assert repository.find(first.action_id, "P1001").status is ActionStatus.SUPERSEDED
    assert repository.find_pending("c1") == second


def test_supersede_only_touches_pending_actions_in_that_conversation(repository):
    confirmed = create(repository, conversation_id="c1")
    repository.mark_confirmed(confirmed.action_id, BOOKED)
    other = create(repository, conversation_id="c2")

    assert repository.supersede("c1") == 0

    assert repository.find(confirmed.action_id, "P1001").status is (
        ActionStatus.CONFIRMED
    )
    assert repository.find(other.action_id, "P1001").status is ActionStatus.PENDING


def test_supersede_with_nothing_pending_returns_zero(repository):
    assert repository.supersede("c1") == 0


def test_find_pending_is_none_without_a_pending_action(repository):
    action = create(repository)
    repository.supersede("c1")

    assert repository.find_pending("c1") is None
    assert repository.find_pending("unknown") is None
    assert repository.find(action.action_id, "P1001") is not None


def test_find_pending_still_returns_an_expired_action(repository):
    action = create(repository)

    found = repository.find_pending("c1")

    assert found == action
    assert found.is_expired(EXPIRES) is True


def test_mark_confirmed_stores_the_result(repository):
    action = create(repository)

    assert repository.mark_confirmed(action.action_id, BOOKED) is True

    found = repository.find(action.action_id, "P1001")
    assert found.status is ActionStatus.CONFIRMED
    assert found.result == BOOKED
    assert repository.find_pending("c1") is None


def test_mark_confirmed_keeps_a_cancelled_result(repository):
    action = create(repository, CANCEL)
    cancelled = BOOKED.model_copy(update={"status": AppointmentStatus.CANCELLED})

    repository.mark_confirmed(action.action_id, cancelled)

    assert repository.find(action.action_id, "P1001").result == cancelled


def test_mark_confirmed_twice_keeps_the_first_result(repository):
    action = create(repository)
    repository.mark_confirmed(action.action_id, BOOKED)
    other = BOOKED.model_copy(update={"appointment_id": 9999})

    assert repository.mark_confirmed(action.action_id, other) is False

    assert repository.find(action.action_id, "P1001").result == BOOKED


def test_mark_confirmed_ignores_superseded_and_unknown_actions(repository):
    action = create(repository)
    repository.supersede("c1")

    assert repository.mark_confirmed(action.action_id, BOOKED) is False
    assert repository.mark_confirmed("missing", BOOKED) is False
    assert repository.find(action.action_id, "P1001").result is None


@pytest.mark.parametrize(
    ("column", "value"),
    [("kind", "delete"), ("status", "expired")],
)
def test_schema_rejects_unknown_kind_and_status(database, column, value):
    values = {"kind": "book", "status": "pending"} | {column: value}

    with (
        database.connect() as connection,
        pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"),
    ):
        connection.execute(
            "INSERT INTO pending_actions (action_id, conversation_id, patient_id, "
            "kind, proposal, status, created_at, expires_at) "
            "VALUES ('a', 'c', 'p', ?, '{}', ?, 't', 't')",
            (values["kind"], values["status"]),
        )
