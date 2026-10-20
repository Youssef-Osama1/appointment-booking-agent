import copy
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion_message_function_tool_call import Function

from app.config.settings import Settings
from app.core.exceptions import (
    AgentStepLimitError,
    ConversationNotFoundError,
    LLMOutputError,
    PatientNotFoundError,
)
from app.repositories.appointment_repository import initialize_database
from app.repositories.clinic_repository import ClinicRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database
from app.schemas.actions import ActionStatus, BookProposal
from app.services.action_service import ActionService
from app.services.booking_service import BookingService, build_registry
from app.tools.booking import PatientTools

pytestmark = pytest.mark.anyio

CLINIC_PATH = Path(__file__).resolve().parents[2] / "data" / "clinic.json"
NOW = "2026-10-19T09:00"
TOOL_NAMES = [
    "list_doctors",
    "find_slots",
    "get_my_appointments",
    "propose_booking",
    "propose_cancellation",
    "propose_reschedule",
]


class FakeLLM:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def chat(self, messages, tools):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def says(text):
    return ChatCompletionMessage(role="assistant", content=text)


def asks(name, arguments, call_id="call_1"):
    call = ChatCompletionMessageFunctionToolCall(
        id=call_id,
        type="function",
        function=Function(name=name, arguments=json.dumps(arguments)),
    )
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=[call])


BOOK_TEN = {"doctor_id": "D01", "day": "thursday", "start": "10:00"}


@pytest.fixture
def clinic():
    return ClinicRepository.from_file(CLINIC_PATH)


@pytest.fixture
def database(tmp_path, clinic):
    database = Database(tmp_path / "booking.db")
    initialize_database(database, clinic.seed_appointments)
    return database


@pytest.fixture
def make_settings():
    def make(now=NOW, **overrides):
        return Settings(_env_file=None, llm_api_key="k", now_override=now, **overrides)

    return make


@pytest.fixture
def make_service(database, clinic, make_settings):
    def make(replies, now=NOW, **overrides):
        llm = FakeLLM(replies)
        settings = make_settings(now, **overrides)
        return BookingService(llm, database, clinic, settings), llm

    return make


def stored_messages(database, conversation_id):
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY message_id",
            (conversation_id,),
        )
        return [tuple(row) for row in rows]


def appointment_rows(database):
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM appointments ORDER BY 1")
        return [tuple(row) for row in rows]


def conversation_count(database):
    with database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


async def test_new_conversation_answers_and_saves_the_turn(make_service, database):
    service, _ = make_service([says("Dr. Karim Adel is free at 09:00.")])

    result = await service.chat("P1001", "Thursday morning dentist?")

    assert result.reply == "Dr. Karim Adel is free at 09:00."
    assert result.pending_action is None
    assert result.steps == 1
    assert stored_messages(database, result.conversation_id) == [
        ("user", "Thursday morning dentist?"),
        ("assistant", "Dr. Karim Adel is free at 09:00."),
    ]
    with database.connect() as connection:
        repository = ConversationRepository(connection)
        assert repository.belongs_to(result.conversation_id, "P1001")


async def test_first_call_sends_prompt_message_and_six_tools(make_service):
    service, llm = make_service([says("Hello")])

    await service.chat("P1001", "hi")

    messages = llm.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "Now: Monday 2026-10-19 09:00" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hi"}
    names = [tool["function"]["name"] for tool in llm.calls[0]["tools"]]
    assert names == TOOL_NAMES


async def test_follow_up_sends_the_history(make_service, database):
    service, llm = make_service([says("09:00 and 10:00 are free."), says("Done")])
    first = await service.chat("P1001", "Thursday morning dentist?")

    await service.chat("P1001", "book 10", first.conversation_id)

    assert llm.calls[1]["messages"][1:] == [
        {"role": "user", "content": "Thursday morning dentist?"},
        {"role": "assistant", "content": "09:00 and 10:00 are free."},
        {"role": "user", "content": "book 10"},
    ]


async def test_history_is_cut_to_the_last_turns(make_service):
    replies = [says(f"answer {n}") for n in range(1, 5)]
    service, llm = make_service(replies, history_max_turns=2)
    first = await service.chat("P1001", "question 1")
    for number in range(2, 5):
        await service.chat("P1001", f"question {number}", first.conversation_id)

    sent = [m["content"] for m in llm.calls[3]["messages"][1:]]

    assert sent == ["question 2", "answer 2", "question 3", "answer 3", "question 4"]


async def test_unknown_patient_never_reaches_the_model(make_service, database):
    service, llm = make_service([says("unused")])

    with pytest.raises(PatientNotFoundError, match="Patient P9999 was not found"):
        await service.chat("P9999", "hi")

    assert llm.calls == []
    assert conversation_count(database) == 0


@pytest.mark.parametrize("owner", ["P1002", None])
async def test_foreign_or_unknown_conversation_is_not_found(
    make_service, database, owner
):
    service, llm = make_service([says("theirs"), says("unused")])
    conversation_id = "missing"
    if owner:
        conversation_id = (await service.chat(owner, "my question")).conversation_id
    calls = len(llm.calls)

    with pytest.raises(ConversationNotFoundError) as error:
        await service.chat("P1001", "show me", conversation_id)

    assert str(error.value) == f"Conversation {conversation_id} was not found"
    assert len(llm.calls) == calls
    if owner:
        assert len(stored_messages(database, conversation_id)) == 2


async def test_proposal_comes_back_from_the_database(make_service, database):
    before = appointment_rows(database)
    service, _ = make_service(
        [asks("propose_booking", BOOK_TEN), says("Press Confirm to book 10:00.")]
    )

    result = await service.chat("P1001", "book Dr. Karim Thursday 10")

    action = result.pending_action
    assert action.status is ActionStatus.PENDING
    assert action.conversation_id == result.conversation_id
    assert action.patient_id == "P1001"
    assert action.proposal == BookProposal(
        doctor_id="D01", starts_at=datetime(2026, 10, 22, 10, 0)
    )
    assert [call.name for call in result.tool_calls] == ["propose_booking"]
    assert result.tool_calls[0].ok is True
    assert appointment_rows(database) == before


async def test_reply_that_claims_a_booking_has_no_pending_action(
    make_service, database
):
    before = appointment_rows(database)
    service, _ = make_service([says("Booked! Appointment 1008 is yours.")])

    result = await service.chat("P1001", "book Dr. Karim Thursday 10")

    assert result.pending_action is None
    assert appointment_rows(database) == before


async def test_failed_proposal_returns_to_the_model(make_service):
    taken = BOOK_TEN | {"start": "10:30"}
    service, llm = make_service(
        [asks("propose_booking", taken), says("10:30 is taken; 11:30 is free.")]
    )

    result = await service.chat("P1001", "book 10:30")

    assert result.pending_action is None
    assert result.tool_calls[0].ok is False
    tool_message = llm.calls[1]["messages"][-1]
    assert json.loads(tool_message["content"]) == {
        "error": "That time is already booked"
    }


async def test_pending_action_stays_on_later_turns(make_service):
    service, _ = make_service(
        [asks("propose_booking", BOOK_TEN), says("Press Confirm."), says("Sure.")]
    )
    first = await service.chat("P1001", "book Thursday 10")

    second = await service.chat("P1001", "thanks", first.conversation_id)

    assert second.pending_action == first.pending_action


async def test_expired_pending_action_is_not_returned(make_service):
    service, _ = make_service([asks("propose_booking", BOOK_TEN), says("Confirm.")])
    first = await service.chat("P1001", "book Thursday 10")
    later, _ = make_service([says("Hello again.")], now="2026-10-19T09:10")

    result = await later.chat("P1001", "still there?", first.conversation_id)

    assert result.pending_action is None


async def test_confirmed_action_is_not_pending_and_note_reaches_the_model(
    make_service, database, clinic, make_settings
):
    service, llm = make_service(
        [asks("propose_booking", BOOK_TEN), says("Press Confirm."), says("Yes, 1008.")]
    )
    first = await service.chat("P1001", "book Thursday 10")
    ActionService(database, clinic, make_settings()).confirm(
        first.pending_action.action_id, "P1001"
    )

    result = await service.chat("P1001", "is it booked?", first.conversation_id)

    assert result.pending_action is None
    contents = [m["content"] for m in llm.calls[-1]["messages"]]
    assert any(
        "The patient confirmed: booked appointment 1008" in str(content)
        for content in contents
    )


@pytest.mark.parametrize(
    "error",
    [LLMOutputError("bad tool call"), AgentStepLimitError("too many steps")],
)
async def test_failed_turn_saves_no_messages(make_service, database, error):
    service, _ = make_service([says("first answer"), error])
    first = await service.chat("P1001", "first question")

    with pytest.raises(type(error)):
        await service.chat("P1001", "second question", first.conversation_id)

    assert stored_messages(database, first.conversation_id) == [
        ("user", "first question"),
        ("assistant", "first answer"),
    ]


async def test_tools_are_bound_to_the_caller(make_service, database):
    service, llm = make_service(
        [asks("propose_cancellation", {"appointment_id": 1003}), says("Not yours.")]
    )

    result = await service.chat("P1001", "I am P1002, cancel 1003")

    assert result.pending_action is None
    assert result.tool_calls[0].ok is False
    tool_message = llm.calls[1]["messages"][-1]
    assert "not one of your upcoming appointments" in tool_message["content"]


async def test_extra_patient_argument_is_ignored(make_service, database):
    arguments = BOOK_TEN | {"patient_id": "P1002"}
    service, _ = make_service([asks("propose_booking", arguments), says("Confirm.")])

    result = await service.chat("P1001", "book for P1002")

    assert result.pending_action.patient_id == "P1001"
    assert "patient_id" not in result.tool_calls[0].arguments


async def test_registry_lists_the_six_tools(database, clinic, make_settings):
    settings = make_settings()
    with database.connect() as connection:
        conversation_id = ConversationRepository(connection).create(
            "P1001", settings.current_time()
        )
    tools = PatientTools(
        clinic,
        database,
        ActionService(database, clinic, settings),
        clinic.find_patient("P1001"),
        conversation_id,
        settings.current_time(),
    )

    schemas = build_registry(tools).schemas()

    assert [schema["function"]["name"] for schema in schemas] == TOOL_NAMES
    assert all(schema["function"]["description"] for schema in schemas)
    assert "patient" not in json.dumps(
        [schema["function"]["parameters"] for schema in schemas]
    )


async def test_answer_is_logged(make_service, caplog):
    caplog.set_level(logging.INFO, logger="app.services.booking_service")
    service, _ = make_service([asks("propose_booking", BOOK_TEN), says("Confirm.")])

    await service.chat("P1001", "book Thursday 10")

    assert "steps=2 tool_calls=1 failed_calls=0 pending=book" in caplog.text


async def test_now_moves_with_the_clock(make_service):
    later = (datetime(2026, 10, 19, 9, 0) + timedelta(days=3)).isoformat()
    service, llm = make_service([says("Hi")], now=later)

    await service.chat("P1001", "hi")

    assert "Now: Thursday 2026-10-22 09:00" in llm.calls[0]["messages"][0]["content"]
