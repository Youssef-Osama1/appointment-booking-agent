import json
import threading

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import (
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion_message_function_tool_call import Function

from app.api.dependencies import get_llm_client
from app.config.settings import Settings, get_settings
from app.core.exceptions import AgentStepLimitError, LLMOutputError
from app.main import app

BOOK_TEN = {"doctor_id": "D01", "day": "thursday", "start": "10:00"}


class FakeLLM:
    def __init__(self):
        self.replies = []
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def says(text):
    return ChatCompletionMessage(role="assistant", content=text)


def asks(name, arguments):
    call = ChatCompletionMessageFunctionToolCall(
        id="call_1",
        type="function",
        function=Function(name=name, arguments=json.dumps(arguments)),
    )
    return ChatCompletionMessage(role="assistant", content=None, tool_calls=[call])


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_llm):
    settings = Settings(
        _env_file=None,
        llm_api_key="k",
        now_override="2026-10-19T09:00",
        database_path=tmp_path / "booking.db",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def propose(client, fake_llm, patient_id="P1001", arguments=BOOK_TEN):
    fake_llm.replies += [asks("propose_booking", arguments), says("Press Confirm.")]
    response = client.post(
        "/chat", json={"patient_id": patient_id, "message": "book Thursday 10"}
    )
    assert response.status_code == 200
    return response.json()


def confirm(client, action_id, patient_id="P1001"):
    return client.post(f"/actions/{action_id}/confirm", json={"patient_id": patient_id})


def appointment_count():
    with app.state.database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]


def test_chat_returns_reply_and_a_new_conversation(client, fake_llm):
    fake_llm.replies.append(says("Dr. Karim Adel is free at 09:00."))

    response = client.post(
        "/chat", json={"patient_id": "P1001", "message": "Thursday morning?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "conversation_id",
        "reply",
        "pending_action",
        "tool_calls",
        "steps",
    }
    assert body["reply"] == "Dr. Karim Adel is free at 09:00."
    assert body["pending_action"] is None
    assert body["tool_calls"] == []
    assert body["steps"] == 1


def test_chat_continues_a_conversation(client, fake_llm):
    fake_llm.replies += [says("first"), says("second")]
    first = client.post("/chat", json={"patient_id": "P1001", "message": "one"})
    conversation_id = first.json()["conversation_id"]

    second = client.post(
        "/chat",
        json={
            "patient_id": "P1001",
            "message": "two",
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id


def test_chat_returns_only_what_the_confirm_button_needs(client, fake_llm):
    body = propose(client, fake_llm)

    action = body["pending_action"]
    assert set(action) == {"action_id", "proposal", "expires_at"}
    assert action["proposal"] == {
        "kind": "book",
        "doctor_id": "D01",
        "starts_at": "2026-10-22T10:00:00",
    }
    assert action["expires_at"] == "2026-10-19T09:10:00"
    assert body["tool_calls"] == [
        {"name": "propose_booking", "arguments": BOOK_TEN, "ok": True}
    ]


def test_chat_proposal_books_nothing(client, fake_llm):
    propose(client, fake_llm)

    assert appointment_count() == 7


def test_confirm_books_the_appointment(client, fake_llm):
    action_id = propose(client, fake_llm)["pending_action"]["action_id"]

    response = confirm(client, action_id)

    assert response.status_code == 200
    assert response.json() == {
        "action_id": action_id,
        "status": "confirmed",
        "appointment": {
            "appointment_id": 1008,
            "patient_id": "P1001",
            "doctor_id": "D01",
            "starts_at": "2026-10-22T10:00:00",
            "status": "booked",
        },
    }
    assert appointment_count() == 8


def test_confirm_twice_returns_the_same_booking(client, fake_llm):
    action_id = propose(client, fake_llm)["pending_action"]["action_id"]

    first = confirm(client, action_id)
    second = confirm(client, action_id)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert appointment_count() == 8


def test_confirm_by_another_patient_is_not_found(client, fake_llm):
    action_id = propose(client, fake_llm)["pending_action"]["action_id"]

    response = confirm(client, action_id, "P1002")

    assert response.status_code == 404
    assert response.json() == {"detail": f"Action {action_id} was not found"}
    assert appointment_count() == 7


def test_confirm_unknown_action_is_not_found(client):
    response = confirm(client, "missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Action missing was not found"}


def test_confirm_taken_slot_is_a_conflict(client, fake_llm):
    mine = propose(client, fake_llm, "P1001")["pending_action"]["action_id"]
    theirs = propose(client, fake_llm, "P1002")["pending_action"]["action_id"]
    confirm(client, theirs, "P1002")

    response = confirm(client, mine)

    assert response.status_code == 409
    assert response.json() == {"detail": "That time is already booked"}


def test_confirm_replaced_proposal_is_gone(client, fake_llm):
    first = propose(client, fake_llm)
    conversation_id = first["conversation_id"]
    fake_llm.replies += [
        asks("propose_booking", BOOK_TEN | {"start": "11:30"}),
        says("Press Confirm for 11:30."),
    ]
    client.post(
        "/chat",
        json={
            "patient_id": "P1001",
            "message": "make it 11:30",
            "conversation_id": conversation_id,
        },
    )

    response = confirm(client, first["pending_action"]["action_id"])

    assert response.status_code == 410
    assert response.json() == {"detail": "This proposal was replaced by a newer one"}


def test_confirm_expired_proposal_is_gone(client, fake_llm):
    action_id = propose(client, fake_llm)["pending_action"]["action_id"]
    later = Settings(
        _env_file=None,
        llm_api_key="k",
        now_override="2026-10-19T09:10",
        database_path=app.state.database._path,
    )
    app.dependency_overrides[get_settings] = lambda: later

    response = confirm(client, action_id)

    assert response.status_code == 410
    assert response.json() == {"detail": "This proposal has expired"}


def test_parallel_confirms_by_two_patients_book_once(client, fake_llm):
    actions = {
        patient_id: propose(client, fake_llm, patient_id)["pending_action"]["action_id"]
        for patient_id in ("P1001", "P1002")
    }
    barrier = threading.Barrier(2)
    statuses = []

    def run(patient_id):
        barrier.wait()
        statuses.append(confirm(client, actions[patient_id], patient_id).status_code)

    threads = [threading.Thread(target=run, args=(p,)) for p in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [200, 409]
    assert appointment_count() == 8


def test_unknown_patient_is_not_found_before_the_model(client, fake_llm):
    response = client.post("/chat", json={"patient_id": "P9999", "message": "hi"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Patient P9999 was not found"}
    assert fake_llm.calls == 0


@pytest.mark.parametrize("owner", ["P1002", None])
def test_foreign_or_unknown_conversation_is_not_found(client, fake_llm, owner):
    conversation_id = "missing"
    if owner:
        fake_llm.replies.append(says("hello"))
        conversation_id = client.post(
            "/chat", json={"patient_id": owner, "message": "hi"}
        ).json()["conversation_id"]
    calls = fake_llm.calls

    response = client.post(
        "/chat",
        json={
            "patient_id": "P1001",
            "message": "show me",
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": f"Conversation {conversation_id} was not found"
    }
    assert fake_llm.calls == calls


def test_patient_id_is_normalized(client, fake_llm):
    fake_llm.replies.append(says("hello"))

    response = client.post("/chat", json={"patient_id": " p1001 ", "message": "hi"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"message": "hi"},
        {"patient_id": "P1001"},
        {"patient_id": "P1001", "message": "   "},
        {"patient_id": "P1001", "message": "x" * 2_001},
        {"patient_id": "1001", "message": "hi"},
        {"patient_id": "P10011", "message": "hi"},
        {"patient_id": "P1001' OR '1'='1", "message": "hi"},
        {"patient_id": "P1001", "message": "hi", "conversation_id": ""},
        {"patient_id": "P1001", "message": "hi", "conversation_id": "c" * 65},
    ],
)
def test_chat_rejects_bad_requests(client, fake_llm, body):
    response = client.post("/chat", json=body)

    assert response.status_code == 422
    assert fake_llm.calls == 0


@pytest.mark.parametrize("body", [{}, {"patient_id": "X1"}])
def test_confirm_rejects_bad_requests(client, body):
    assert client.post("/actions/abc/confirm", json=body).status_code == 422


@pytest.mark.parametrize(
    "error",
    [LLMOutputError("bad tool call"), AgentStepLimitError("too many steps")],
)
def test_model_failures_become_502(client, fake_llm, error):
    fake_llm.replies.append(error)

    response = client.post("/chat", json={"patient_id": "P1001", "message": "hi"})

    assert response.status_code == 502
    assert response.json() == {"detail": str(error)}


def test_openapi_lists_the_booking_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert "/chat" in paths
    assert "/actions/{action_id}/confirm" in paths
