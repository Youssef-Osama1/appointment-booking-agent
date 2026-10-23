from datetime import datetime

import pytest

from app.mcp_server.approval import (
    CONFIRM_COMMAND,
    confirm_in_terminal,
    describe,
    when,
)
from app.repositories.action_repository import ActionRepository
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.actions import ActionStatus

THURSDAY_NINE = ("P1001", "D01", "2026-10-22T09:00")


class Terminal:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.fixture
def terminal(monkeypatch):
    def use(answer="y", tty=True):
        prompts = []
        monkeypatch.setattr("sys.stdin", Terminal(tty))

        def fake_input(prompt):
            prompts.append(prompt)
            return answer

        monkeypatch.setattr("builtins.input", fake_input)
        return prompts

    return use


@pytest.fixture
def approve(clinic, database, settings):
    def run(patient_id="P1001"):
        return confirm_in_terminal(
            clinic, database, settings, clinic.find_patient(patient_id)
        )

    return run


@pytest.fixture
def proposed(make_tools, conversation_id):
    make_tools(conversation_id).propose_booking("D01", "thursday", "09:00")
    return conversation_id


def booked(rows):
    return rows(
        "SELECT patient_id, doctor_id, starts_at FROM appointments "
        "WHERE status = 'booked'"
    )


def test_confirm_command_matches_the_module_entry_point():
    assert CONFIRM_COMMAND == "uv run python -m app.mcp_server confirm"


def test_when_names_the_weekday_date_and_time():
    assert when(datetime(2026, 10, 22, 9, 0)) == "Thursday 2026-10-22 at 09:00"


def test_describe_a_booking(clinic, database, pending, proposed):
    question = describe(pending(proposed), clinic, database)

    assert question == "Book Dr. Karim Adel on Thursday 2026-10-22 at 09:00?"


def test_describe_a_cancellation(
    clinic, database, pending, make_tools, conversation_id
):
    make_tools(conversation_id).propose_cancellation(1002)

    question = describe(pending(conversation_id), clinic, database)

    assert question == (
        "Cancel your appointment with Dr. Ahmed Farouk on Saturday 2026-10-24 at 11:00?"
    )


def test_describe_a_reschedule(clinic, database, pending, make_tools, conversation_id):
    make_tools(conversation_id).propose_reschedule(1002, "tuesday", "12:00")

    question = describe(pending(conversation_id), clinic, database)

    assert question == (
        "Move your appointment with Dr. Ahmed Farouk from Saturday 2026-10-24 "
        "at 11:00 to Tuesday 2026-10-20 at 12:00?"
    )


def test_yes_books_the_appointment(terminal, approve, rows, pending, proposed, capsys):
    prompts = terminal("y")

    assert approve() == 0
    assert prompts == ["Book Dr. Karim Adel on Thursday 2026-10-22 at 09:00? [y/N] "]
    assert THURSDAY_NINE in booked(rows)
    assert pending(proposed) is None
    assert "Done. Appointment" in capsys.readouterr().out


def test_yes_accepts_spaces_and_capitals(terminal, approve, rows, proposed):
    terminal(" Y ")

    assert approve() == 0
    assert THURSDAY_NINE in booked(rows)


def test_yes_writes_a_confirmation_note(terminal, approve, rows, proposed):
    terminal("y")

    approve()

    notes = rows("SELECT role FROM messages WHERE conversation_id = ?", proposed)
    assert notes == [("system",)]


@pytest.mark.parametrize("answer", ["", "n", "no", "yes please", "N"])
def test_anything_but_y_changes_nothing(
    terminal, approve, rows, pending, proposed, capsys, answer
):
    terminal(answer)
    before = booked(rows)

    assert approve() == 0
    assert booked(rows) == before
    assert pending(proposed).status is ActionStatus.PENDING
    assert "Nothing changed." in capsys.readouterr().out


def test_piped_input_is_refused_without_asking(
    terminal, approve, rows, pending, proposed, capsys
):
    prompts = terminal("y", tty=False)
    before = booked(rows)

    assert approve() == 1
    assert prompts == []
    assert booked(rows) == before
    assert pending(proposed).status is ActionStatus.PENDING
    assert "Run this command yourself in a terminal." in capsys.readouterr().out


def test_nothing_waiting(terminal, approve, capsys):
    prompts = terminal("y")

    assert approve() == 1
    assert prompts == []
    assert "Nothing is waiting for approval." in capsys.readouterr().out


def test_an_expired_proposal_is_not_offered(
    terminal, approve, settings, rows, proposed, capsys
):
    prompts = terminal("y")
    settings.now_override = datetime(2026, 10, 19, 9, 10)
    before = booked(rows)

    assert approve() == 1
    assert prompts == []
    assert booked(rows) == before
    assert "Nothing is waiting for approval." in capsys.readouterr().out


def test_another_patients_proposal_is_not_offered(
    terminal, approve, rows, make_tools, open_conversation
):
    other = open_conversation("P1002")
    make_tools(other, "P1002").propose_booking("D01", "thursday", "09:00")
    prompts = terminal("y")

    assert approve("P1001") == 1
    assert prompts == []
    assert ("P1002", "D01", "2026-10-22T09:00") not in booked(rows)


def test_the_latest_proposal_is_offered(
    terminal, approve, rows, make_tools, open_conversation
):
    make_tools(open_conversation()).propose_booking("D01", "thursday", "09:00")
    make_tools(open_conversation()).propose_booking("D01", "thursday", "09:30")
    prompts = terminal("y")

    approve()

    assert prompts == ["Book Dr. Karim Adel on Thursday 2026-10-22 at 09:30? [y/N] "]


def test_a_slot_taken_meanwhile_is_reported(
    terminal, approve, database, rows, proposed, capsys
):
    terminal("y")
    with database.transaction() as connection:
        AppointmentRepository(connection).create(
            "P1002", "D01", datetime(2026, 10, 22, 9, 0)
        )

    assert approve() == 1
    assert THURSDAY_NINE not in booked(rows)
    assert "That time is already booked" in capsys.readouterr().out


def test_approving_twice_books_once(terminal, approve, database, rows, proposed):
    terminal("y")

    assert approve() == 0
    assert approve() == 1

    with database.connect() as connection:
        assert ActionRepository(connection).find_latest_pending("P1001") is None
    assert rows(
        "SELECT COUNT(*) FROM appointments WHERE starts_at = '2026-10-22T09:00'"
    ) == [(1,)]
