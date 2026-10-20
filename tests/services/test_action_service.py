import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.core.exceptions import (
    ActionConflictError,
    ActionExpiredError,
    ActionNotFoundError,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import (
    AppointmentRepository,
    initialize_database,
)
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.schemas.actions import (
    ActionStatus,
    BookProposal,
    CancelProposal,
    RescheduleProposal,
)
from app.schemas.clinic import AppointmentStatus
from app.services.action_service import ActionService

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = "2026-10-19T09:00"
THURSDAY_TEN = datetime(2026, 10, 22, 10, 0)
BOOK = BookProposal(doctor_id="D01", starts_at=THURSDAY_TEN)
CANCEL = CancelProposal(appointment_id=1002)
RESCHEDULE = RescheduleProposal(
    appointment_id=1002, starts_at=datetime(2026, 10, 27, 12, 0)
)
CONVERSATIONS = {
    "c1": "P1001",
    "c2": "P1001",
    "c-P1001": "P1001",
    "c-P1002": "P1002",
    "c-P1003": "P1003",
}


@pytest.fixture
def clinic():
    return ClinicRepository.from_file(CLINIC_PATH)


@pytest.fixture
def database(tmp_path, clinic):
    database = Database(tmp_path / "booking.db")
    initialize_database(database, clinic.seed_appointments)
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO conversations (conversation_id, patient_id, created_at) "
            "VALUES (?, ?, '2026-10-19T08:00:00')",
            CONVERSATIONS.items(),
        )
    return database


@pytest.fixture
def make_service(database, clinic):
    def make(now=NOW, ttl=10):
        settings = Settings(
            _env_file=None,
            llm_api_key="k",
            now_override=now,
            pending_action_ttl_minutes=ttl,
        )
        return ActionService(database, clinic, settings)

    return make


@pytest.fixture
def service(make_service):
    return make_service()


def appointment_rows(database):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT appointment_id, patient_id, doctor_id, starts_at, status "
            "FROM appointments ORDER BY appointment_id"
        )
        return [tuple(row) for row in rows]


def find_action(database, action_id, patient_id="P1001"):
    with database.connect() as connection:
        return ActionRepository(connection).find(action_id, patient_id)


def history(database, conversation_id="c1"):
    with database.connect() as connection:
        return ConversationRepository(connection).recent(conversation_id, 10)


def system_notes(database, conversation_id="c1"):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'system'",
            (conversation_id,),
        )
        return [row["content"] for row in rows]


def find_appointment(database, appointment_id, patient_id="P1001"):
    with database.connect() as connection:
        return AppointmentRepository(connection).find(appointment_id, patient_id)


def test_propose_stores_a_pending_action(service, database):
    action = service.propose("c1", "P1001", BOOK)

    assert action.status is ActionStatus.PENDING
    assert action.proposal == BOOK
    assert action.created_at == datetime(2026, 10, 19, 9, 0)
    assert action.expires_at == datetime(2026, 10, 19, 9, 10)
    assert find_action(database, action.action_id) == action


def test_propose_uses_the_configured_ttl(make_service):
    action = make_service(ttl=30).propose("c1", "P1001", BOOK)

    assert action.expires_at == datetime(2026, 10, 19, 9, 30)


@pytest.mark.parametrize("proposal", [BOOK, CANCEL, RESCHEDULE])
def test_propose_never_touches_appointments(service, database, proposal):
    before = appointment_rows(database)

    service.propose("c1", "P1001", proposal)

    assert appointment_rows(database) == before


def test_propose_replaces_the_previous_proposal(service, database):
    first = service.propose("c1", "P1001", BOOK)

    second = service.propose("c1", "P1001", CANCEL)

    assert find_action(database, first.action_id).status is ActionStatus.SUPERSEDED
    assert find_action(database, second.action_id).status is ActionStatus.PENDING


def test_propose_keeps_proposals_in_other_conversations(service, database):
    first = service.propose("c1", "P1001", BOOK)

    service.propose("c2", "P1001", CANCEL)

    assert find_action(database, first.action_id).status is ActionStatus.PENDING


@pytest.mark.parametrize(
    ("proposal", "message"),
    [
        (
            BookProposal(doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 30)),
            "already booked",
        ),
        (BookProposal(doctor_id="D99", starts_at=THURSDAY_TEN), "Unknown doctor"),
        (CancelProposal(appointment_id=1003), "not one of your upcoming"),
        (
            RescheduleProposal(
                appointment_id=1003, starts_at=datetime(2026, 10, 22, 9, 0)
            ),
            "not one of your upcoming",
        ),
    ],
)
def test_invalid_proposal_is_rejected_and_stores_nothing(
    service, database, proposal, message
):
    with pytest.raises(ActionConflictError, match=message):
        service.propose("c1", "P1001", proposal)

    with database.connect() as connection:
        assert ActionRepository(connection).find_pending("c1") is None


def test_invalid_proposal_keeps_the_previous_one(service, database):
    first = service.propose("c1", "P1001", BOOK)
    taken = BookProposal(doctor_id="D01", starts_at=datetime(2026, 10, 22, 11, 0))

    with pytest.raises(ActionConflictError):
        service.propose("c1", "P1001", taken)

    assert find_action(database, first.action_id).status is ActionStatus.PENDING


def test_confirm_books_the_appointment(service, database):
    action = service.propose("c1", "P1001", BOOK)

    confirmed = service.confirm(action.action_id, "P1001")

    assert confirmed.status is ActionStatus.CONFIRMED
    assert confirmed.result.appointment_id == 1008
    assert confirmed.result.starts_at == THURSDAY_TEN
    assert confirmed.result.status is AppointmentStatus.BOOKED
    assert find_appointment(database, 1008) == confirmed.result
    assert find_action(database, action.action_id) == confirmed


def test_confirm_cancels_the_appointment(service, database):
    action = service.propose("c1", "P1001", CANCEL)

    confirmed = service.confirm(action.action_id, "P1001")

    assert confirmed.result.appointment_id == 1002
    assert confirmed.result.status is AppointmentStatus.CANCELLED
    assert find_appointment(database, 1002).status is AppointmentStatus.CANCELLED


def test_confirm_reschedules_the_appointment(service, database):
    action = service.propose("c1", "P1001", RESCHEDULE)

    confirmed = service.confirm(action.action_id, "P1001")

    assert confirmed.result.appointment_id == 1002
    assert confirmed.result.doctor_id == "D03"
    assert confirmed.result.starts_at == datetime(2026, 10, 27, 12, 0)
    assert find_appointment(database, 1002) == confirmed.result


def test_confirm_twice_books_once_and_returns_the_same_result(service, database):
    action = service.propose("c1", "P1001", BOOK)

    first = service.confirm(action.action_id, "P1001")
    rows = appointment_rows(database)
    second = service.confirm(action.action_id, "P1001")

    assert second == first
    assert appointment_rows(database) == rows
    assert len(rows) == 8


def test_confirm_repeat_still_works_after_the_deadline(make_service):
    action = make_service().propose("c1", "P1001", BOOK)
    first = make_service().confirm(action.action_id, "P1001")

    again = make_service(now="2026-10-19T12:00").confirm(action.action_id, "P1001")

    assert again == first


@pytest.mark.parametrize(
    ("action_id", "patient_id"), [(None, "P1002"), ("missing", "P1001")]
)
def test_confirm_hides_other_patients_and_unknown_actions(
    service, database, action_id, patient_id
):
    action = service.propose("c1", "P1001", BOOK)
    before = appointment_rows(database)
    action_id = action_id or action.action_id

    with pytest.raises(ActionNotFoundError) as error:
        service.confirm(action_id, patient_id)

    assert str(error.value) == f"Action {action_id} was not found"
    assert appointment_rows(database) == before
    assert find_action(database, action.action_id).status is ActionStatus.PENDING


def test_confirm_rejects_a_superseded_proposal(service, database):
    first = service.propose("c1", "P1001", BOOK)
    service.propose("c1", "P1001", CANCEL)
    before = appointment_rows(database)

    with pytest.raises(ActionExpiredError, match="replaced by a newer one"):
        service.confirm(first.action_id, "P1001")

    assert appointment_rows(database) == before


@pytest.mark.parametrize(
    ("now", "expired"),
    [("2026-10-19T09:09:59", False), ("2026-10-19T09:10", True)],
)
def test_confirm_respects_the_deadline(make_service, database, now, expired):
    action = make_service().propose("c1", "P1001", BOOK)
    later = make_service(now=now)

    if expired:
        with pytest.raises(ActionExpiredError, match="has expired"):
            later.confirm(action.action_id, "P1001")
        assert find_action(database, action.action_id).status is ActionStatus.PENDING
    else:
        assert later.confirm(action.action_id, "P1001").status is (
            ActionStatus.CONFIRMED
        )


def test_confirm_rechecks_the_slot(service, database):
    mine = service.propose("c1", "P1001", BOOK)
    theirs = service.propose("c-P1002", "P1002", BOOK)
    service.confirm(theirs.action_id, "P1002")
    before = appointment_rows(database)

    with pytest.raises(ActionConflictError, match="already booked"):
        service.confirm(mine.action_id, "P1001")

    assert appointment_rows(database) == before
    assert find_action(database, mine.action_id).status is ActionStatus.PENDING


def test_confirm_rechecks_a_cancelled_appointment(service, database):
    cancel = service.propose("c1", "P1001", CANCEL)
    move = service.propose("c2", "P1001", RESCHEDULE)
    service.confirm(cancel.action_id, "P1001")

    with pytest.raises(ActionConflictError, match="not one of your upcoming"):
        service.confirm(move.action_id, "P1001")

    assert find_appointment(database, 1002).starts_at == datetime(2026, 10, 24, 11)


def test_confirm_rejects_a_time_that_passed(make_service):
    early = BookProposal(doctor_id="D05", starts_at=datetime(2026, 10, 19, 10, 0))
    action = make_service(now="2026-10-19T09:55").propose("c1", "P1001", early)

    with pytest.raises(ActionConflictError, match="already passed"):
        make_service(now="2026-10-19T10:00").confirm(action.action_id, "P1001")


def test_confirm_turns_a_unique_violation_into_a_conflict(
    service, database, monkeypatch
):
    action = service.propose("c1", "P1001", BOOK)
    with database.connect() as connection:
        AppointmentRepository(connection).create("P1002", "D01", THURSDAY_TEN)
    monkeypatch.setattr(AppointmentRepository, "is_doctor_booked", lambda *args: False)

    with pytest.raises(ActionConflictError, match="no longer available"):
        service.confirm(action.action_id, "P1001")

    assert find_action(database, action.action_id).status is ActionStatus.PENDING


def test_two_patients_confirming_one_slot_at_once_book_it_once(service, database):
    actions = {
        patient_id: service.propose(f"c-{patient_id}", patient_id, BOOK)
        for patient_id in ("P1001", "P1002", "P1003")
    }
    barrier = threading.Barrier(len(actions))
    outcomes = []

    def confirm(patient_id):
        barrier.wait()
        try:
            service.confirm(actions[patient_id].action_id, patient_id)
            outcomes.append("confirmed")
        except ActionConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=confirm, args=(p,)) for p in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["confirmed", "conflict", "conflict"]
    booked = [row for row in appointment_rows(database) if row[3] == "2026-10-22T10:00"]
    assert len(booked) == 1


def test_one_patient_confirming_twice_at_once_books_once(service, database):
    action = service.propose("c1", "P1001", BOOK)
    barrier = threading.Barrier(2)
    results = []

    def confirm():
        barrier.wait()
        results.append(service.confirm(action.action_id, "P1001"))

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results[0] == results[1]
    assert len(appointment_rows(database)) == 8


def test_propose_and_confirm_are_logged(service, caplog):
    caplog.set_level(logging.INFO, logger="app.services.action_service")

    action = service.propose("c1", "P1001", BOOK)
    service.confirm(action.action_id, "P1001")
    service.confirm(action.action_id, "P1001")

    messages = [record.getMessage() for record in caplog.records]
    assert any("Proposed action" in m and "kind=book" in m for m in messages)
    assert any("appointment_id=1008" in m for m in messages)
    assert any("already confirmed" in m for m in messages)


@pytest.mark.parametrize(
    ("proposal", "note"),
    [
        (
            BOOK,
            (
                "The patient confirmed: booked appointment 1008 with Dr. Karim Adel "
                "on Thursday 2026-10-22 at 10:00."
            ),
        ),
        (
            CANCEL,
            (
                "The patient confirmed: cancelled appointment 1002 with "
                "Dr. Ahmed Farouk on Saturday 2026-10-24 at 11:00."
            ),
        ),
        (
            RESCHEDULE,
            (
                "The patient confirmed: moved appointment 1002 with Dr. Ahmed Farouk "
                "to Tuesday 2026-10-27 at 12:00."
            ),
        ),
    ],
)
def test_confirm_writes_a_note_into_the_conversation(service, database, proposal, note):
    action = service.propose("c1", "P1001", proposal)

    service.confirm(action.action_id, "P1001")

    assert history(database) == []
    assert system_notes(database) == [note]


def test_confirm_note_goes_to_the_action_conversation_only(service, database):
    action = service.propose("c2", "P1001", BOOK)

    service.confirm(action.action_id, "P1001")

    assert system_notes(database, "c1") == []
    assert len(system_notes(database, "c2")) == 1


def test_confirm_twice_writes_one_note(service, database):
    action = service.propose("c1", "P1001", BOOK)

    service.confirm(action.action_id, "P1001")
    service.confirm(action.action_id, "P1001")

    assert len(system_notes(database)) == 1


def test_failed_confirm_writes_no_note(service, database):
    mine = service.propose("c1", "P1001", BOOK)
    theirs = service.propose("c-P1002", "P1002", BOOK)
    service.confirm(theirs.action_id, "P1002")

    with pytest.raises(ActionConflictError):
        service.confirm(mine.action_id, "P1001")

    assert system_notes(database) == []


def test_propose_writes_nothing_into_the_conversation(service, database):
    service.propose("c1", "P1001", BOOK)

    assert system_notes(database) == []


def test_confirm_without_a_conversation_books_nothing(service, database):
    action = service.propose("ghost", "P1001", BOOK)
    before = appointment_rows(database)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        service.confirm(action.action_id, "P1001")

    assert appointment_rows(database) == before
    assert find_action(database, action.action_id).status is ActionStatus.PENDING
