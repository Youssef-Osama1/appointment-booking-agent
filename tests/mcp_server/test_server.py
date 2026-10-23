import json
from datetime import datetime

import mcp_types as types
import pytest
from mcp import Client

from app.mcp_server.server import NO_FORMS, build_server
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.actions import ActionStatus

pytestmark = pytest.mark.anyio

MODES = ["legacy", "2026-07-28"]
READ_TOOLS = {"list_doctors", "find_slots", "get_my_appointments"}
WRITE_TOOLS = {
    "propose_booking",
    "propose_cancellation",
    "propose_reschedule",
    "request_confirmation",
}
BOOK_THURSDAY = {"doctor_id": "D01", "day": "thursday", "start": "09:00"}
BOOK_QUESTION = "Book Dr. Karim Adel on Thursday 2026-10-22 at 09:00?"
THURSDAY_NINE = ("P1001", "D01", "2026-10-22T09:00")
CANCEL_QUESTION = (
    "Cancel your appointment with Dr. Ahmed Farouk on Saturday 2026-10-24 at 11:00?"
)
MOVE_QUESTION = (
    "Move your appointment with Dr. Ahmed Farouk from Saturday 2026-10-24 "
    "at 11:00 to Tuesday 2026-10-20 at 12:00?"
)


@pytest.fixture
def server(clinic, database, settings, conversation_id):
    return build_server(
        clinic, database, settings, clinic.find_patient("P1001"), conversation_id
    )


class Patient:
    def __init__(self, action="accept"):
        self.action = action
        self.questions = []

    async def __call__(self, context, params):
        self.questions.append(params.message)
        if self.action == "accept":
            return types.ElicitResult(action="accept", content={})
        return types.ElicitResult(action=self.action)


def payload(result):
    return json.loads(result.content[0].text)


def text(result):
    return result.content[0].text


def booked(rows):
    return rows(
        "SELECT patient_id, doctor_id, starts_at FROM appointments "
        "WHERE status = 'booked' ORDER BY appointment_id"
    )


async def test_lists_read_and_write_tools(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == READ_TOOLS | WRITE_TOOLS
    for tool in tools:
        read_only = bool(tool.annotations and tool.annotations.read_only_hint)
        assert read_only == (tool.name in READ_TOOLS)


async def test_no_tool_lets_the_model_choose_the_patient(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        assert "patient" not in json.dumps(tool.input_schema).casefold()


async def test_request_confirmation_takes_no_arguments(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    tool = next(tool for tool in tools if tool.name == "request_confirmation")
    assert tool.input_schema.get("properties", {}) == {}


async def test_find_slots_returns_free_times(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "find_slots", {"day": "thursday", "doctor_id": "D01"}
        )

    assert not result.is_error
    times = payload(result)["doctors"][0]["free_times"]
    assert "09:00" in times
    assert "10:30" not in times


async def test_tool_error_message_reaches_the_model(server):
    async with Client(server) as client:
        result = await client.call_tool("find_slots", {"day": "thursday"})

    assert result.is_error
    assert "Give a specialty or a doctor_id" in text(result)


async def test_unknown_day_error_reaches_the_model(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "find_slots", {"day": "someday", "specialty": "dentistry"}
        )

    assert result.is_error
    assert "Unknown day" in text(result)


async def test_list_doctors_filters_by_specialty(server):
    async with Client(server) as client:
        result = await client.call_tool("list_doctors", {"specialty": "dentistry"})

    assert [d["doctor_id"] for d in payload(result)["doctors"]] == ["D01", "D02"]


async def test_get_my_appointments_shows_only_the_bound_patient(server):
    async with Client(server) as client:
        result = await client.call_tool("get_my_appointments", {})

    data = payload(result)
    assert data["name"] == "Omar Khaled"
    assert [a["appointment_id"] for a in data["appointments"]] == [1002]


async def test_tools_read_the_clock_on_every_call(server, settings):
    today_d01 = {"day": "today", "doctor_id": "D01"}
    async with Client(server) as client:
        first = payload(await client.call_tool("find_slots", today_d01))
        settings.now_override = datetime(2026, 10, 19, 11, 0)
        later = payload(await client.call_tool("find_slots", today_d01))

    assert "09:30" in first["doctors"][0]["free_times"]
    assert "09:30" not in later["doctors"][0]["free_times"]


async def test_propose_booking_only_creates_a_pending_action(
    server, rows, pending, conversation_id
):
    before = booked(rows)
    async with Client(server) as client:
        result = await client.call_tool("propose_booking", BOOK_THURSDAY)

    assert payload(result)["status"] == "awaiting_confirmation"
    assert booked(rows) == before
    assert pending(conversation_id).proposal.doctor_id == "D01"


async def test_propose_booking_rejects_a_taken_slot(server):
    taken = {"doctor_id": "D01", "day": "thursday", "start": "10:30"}
    async with Client(server) as client:
        result = await client.call_tool("propose_booking", taken)

    assert result.is_error
    assert "already booked" in text(result)


async def test_request_confirmation_without_a_proposal_fails(server):
    patient = Patient()
    async with Client(server, elicitation_callback=patient) as client:
        result = await client.call_tool("request_confirmation", {})

    assert result.is_error
    assert "call a propose tool first" in text(result)
    assert patient.questions == []


@pytest.mark.parametrize("mode", MODES)
async def test_approval_books_the_appointment(
    server, rows, pending, conversation_id, mode
):
    patient = Patient()
    async with Client(server, elicitation_callback=patient, mode=mode) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        action_id = pending(conversation_id).action_id
        result = await client.call_tool("request_confirmation", {})

    assert not result.is_error
    data = payload(result)
    assert data["status"] == "confirmed"
    assert data["appointment"]["doctor_id"] == "D01"
    assert THURSDAY_NINE in booked(rows)
    assert patient.questions == [BOOK_QUESTION]
    assert pending(conversation_id) is None
    assert rows(
        "SELECT status FROM pending_actions WHERE action_id = ?", action_id
    ) == [(ActionStatus.CONFIRMED,)]


@pytest.mark.parametrize("mode", MODES)
async def test_approval_runs_the_booking_exactly_once(server, rows, mode):
    async with Client(server, elicitation_callback=Patient(), mode=mode) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        await client.call_tool("request_confirmation", {})

    assert rows(
        "SELECT COUNT(*) FROM appointments WHERE starts_at = '2026-10-22T09:00'"
    ) == [(1,)]
    assert rows("SELECT COUNT(*) FROM pending_actions") == [(1,)]


async def test_approval_writes_a_confirmation_note(server, rows, conversation_id):
    async with Client(server, elicitation_callback=Patient()) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        await client.call_tool("request_confirmation", {})

    notes = rows(
        "SELECT role, content FROM messages WHERE conversation_id = ?",
        conversation_id,
    )
    assert len(notes) == 1
    assert notes[0][0] == "system"
    assert notes[0][1].startswith("The patient confirmed: booked appointment")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_refusal_changes_nothing(
    server, rows, pending, conversation_id, action, mode
):
    before = booked(rows)
    patient = Patient(action)
    async with Client(server, elicitation_callback=patient, mode=mode) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        result = await client.call_tool("request_confirmation", {})

    assert not result.is_error
    assert payload(result) == {"status": "not_approved", "note": "Nothing changed."}
    assert booked(rows) == before
    assert pending(conversation_id).status is ActionStatus.PENDING


@pytest.mark.parametrize("mode", MODES)
async def test_client_without_forms_gets_the_terminal_command(
    server, rows, pending, conversation_id, mode
):
    before = booked(rows)
    async with Client(server, mode=mode) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        result = await client.call_tool("request_confirmation", {})

    assert result.is_error
    assert NO_FORMS in text(result)
    assert "uv run python -m app.mcp_server confirm" in text(result)
    assert booked(rows) == before
    assert pending(conversation_id).status is ActionStatus.PENDING


async def test_a_new_proposal_replaces_the_one_waiting(server, rows):
    patient = Patient()
    later = {"doctor_id": "D01", "day": "thursday", "start": "09:30"}
    async with Client(server, elicitation_callback=patient) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        await client.call_tool("propose_booking", later)
        await client.call_tool("request_confirmation", {})

    assert patient.questions == ["Book Dr. Karim Adel on Thursday 2026-10-22 at 09:30?"]
    assert THURSDAY_NINE not in booked(rows)
    assert ("P1001", "D01", "2026-10-22T09:30") in booked(rows)


async def test_approving_a_slot_taken_meanwhile_reports_the_conflict(
    server, database, rows
):
    async with Client(server, elicitation_callback=Patient()) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        with database.transaction() as connection:
            AppointmentRepository(connection).create(
                "P1002", "D01", datetime(2026, 10, 22, 9, 0)
            )
        result = await client.call_tool("request_confirmation", {})

    assert result.is_error
    assert "That time is already booked" in text(result)
    assert THURSDAY_NINE not in booked(rows)


async def test_an_expired_proposal_cannot_be_approved(server, settings, rows):
    patient = Patient()
    before = booked(rows)
    async with Client(server, elicitation_callback=patient) as client:
        await client.call_tool("propose_booking", BOOK_THURSDAY)
        settings.now_override = datetime(2026, 10, 19, 9, 10)
        result = await client.call_tool("request_confirmation", {})

    assert result.is_error
    assert "call a propose tool first" in text(result)
    assert patient.questions == []
    assert booked(rows) == before


async def test_cancellation_question_names_the_current_appointment(server, rows):
    patient = Patient()
    async with Client(server, elicitation_callback=patient) as client:
        await client.call_tool("propose_cancellation", {"appointment_id": 1002})
        result = await client.call_tool("request_confirmation", {})

    assert patient.questions == [CANCEL_QUESTION]
    assert payload(result)["appointment"]["status"] == "cancelled"
    assert rows("SELECT status FROM appointments WHERE appointment_id = 1002") == [
        ("cancelled",)
    ]


async def test_reschedule_question_names_both_times(server, rows):
    patient = Patient()
    move = {"appointment_id": 1002, "day": "tuesday", "start": "12:00"}
    async with Client(server, elicitation_callback=patient) as client:
        await client.call_tool("propose_reschedule", move)
        await client.call_tool("request_confirmation", {})

    assert patient.questions == [MOVE_QUESTION]
    assert rows("SELECT starts_at FROM appointments WHERE appointment_id = 1002") == [
        ("2026-10-20T12:00",)
    ]


async def test_cannot_propose_cancelling_another_patients_appointment(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "propose_cancellation", {"appointment_id": 1003}
        )

    assert result.is_error


async def test_cannot_approve_a_proposal_from_another_conversation(
    clinic, database, settings, open_conversation, make_tools, rows
):
    make_tools(open_conversation()).propose_booking("D01", "thursday", "09:00")
    server = build_server(
        clinic, database, settings, clinic.find_patient("P1001"), open_conversation()
    )
    patient = Patient()
    async with Client(server, elicitation_callback=patient) as client:
        result = await client.call_tool("request_confirmation", {})

    assert result.is_error
    assert patient.questions == []
    assert THURSDAY_NINE not in booked(rows)
