import sqlite3
from datetime import datetime
from uuid import UUID

import pytest

from app.repositories.conversation_repository import ConversationRepository
from app.repositories.database import Database

NOW = datetime(2026, 10, 19, 9, 0, 45)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "booking.db")
    database.create_schema()
    return database


@pytest.fixture
def repository(database):
    with database.connect() as connection:
        yield ConversationRepository(connection)


def add_turns(repository, conversation_id, turns):
    for number in range(1, turns + 1):
        repository.append(conversation_id, "user", f"question {number}", NOW)
        repository.append(conversation_id, "assistant", f"answer {number}", NOW)


def test_create_returns_a_new_uuid(repository):
    first = repository.create("P1001", NOW)
    second = repository.create("P1001", NOW)

    assert UUID(first).version == 4
    assert first != second


def test_create_stores_the_owner_and_time(database, repository):
    conversation_id = repository.create("P1001", NOW)

    with database.connect() as connection:
        row = connection.execute("SELECT * FROM conversations").fetchone()
    assert dict(row) == {
        "conversation_id": conversation_id,
        "patient_id": "P1001",
        "created_at": "2026-10-19T09:00:45",
    }


def test_belongs_to_only_the_owner(repository):
    conversation_id = repository.create("P1001", NOW)

    assert repository.belongs_to(conversation_id, "P1001") is True
    assert repository.belongs_to(conversation_id, "P1002") is False
    assert repository.belongs_to("missing", "P1001") is False
    assert repository.belongs_to(conversation_id, "") is False


def test_new_conversation_has_no_history(repository):
    conversation_id = repository.create("P1001", NOW)

    assert repository.recent(conversation_id, 10) == []


def test_recent_returns_messages_in_order_as_chat_dicts(repository):
    conversation_id = repository.create("P1001", NOW)
    repository.append(conversation_id, "user", "Thursday morning?", NOW)
    repository.append(conversation_id, "assistant", "9:00 and 10:00 are free.", NOW)
    repository.append(conversation_id, "user", "Book 10", NOW)
    repository.append(conversation_id, "assistant", "Press confirm.", NOW)
    repository.append(conversation_id, "system", "The patient confirmed.", NOW)

    assert repository.recent(conversation_id, 10) == [
        {"role": "user", "content": "Thursday morning?"},
        {"role": "assistant", "content": "9:00 and 10:00 are free."},
        {"role": "user", "content": "Book 10"},
        {"role": "assistant", "content": "Press confirm."},
        {"role": "system", "content": "The patient confirmed."},
    ]


def test_recent_keeps_the_last_turns_only(repository):
    conversation_id = repository.create("P1001", NOW)
    add_turns(repository, conversation_id, 5)

    history = repository.recent(conversation_id, 2)

    assert history == [
        {"role": "user", "content": "question 4"},
        {"role": "assistant", "content": "answer 4"},
        {"role": "user", "content": "question 5"},
        {"role": "assistant", "content": "answer 5"},
    ]


def test_recent_always_starts_with_a_user_message(repository):
    conversation_id = repository.create("P1001", NOW)
    add_turns(repository, conversation_id, 3)
    repository.append(conversation_id, "system", "The patient confirmed.", NOW)
    repository.append(conversation_id, "user", "question 4", NOW)

    history = repository.recent(conversation_id, 2)

    assert history[0] == {"role": "user", "content": "question 3"}
    assert [m["role"] for m in history] == ["user", "assistant", "system", "user"]


def test_recent_counts_turns_not_messages(repository):
    conversation_id = repository.create("P1001", NOW)
    repository.append(conversation_id, "user", "question 1", NOW)
    for number in range(4):
        repository.append(conversation_id, "system", f"note {number}", NOW)

    assert len(repository.recent(conversation_id, 1)) == 5


def test_recent_returns_everything_when_shorter_than_the_limit(repository):
    conversation_id = repository.create("P1001", NOW)
    add_turns(repository, conversation_id, 3)

    assert len(repository.recent(conversation_id, 10)) == 6


def test_recent_keeps_conversations_apart(repository):
    mine = repository.create("P1001", NOW)
    theirs = repository.create("P1002", NOW)
    repository.append(mine, "user", "my question", NOW)
    repository.append(theirs, "user", "their question", NOW)
    repository.append(mine, "assistant", "my answer", NOW)

    assert repository.recent(mine, 10) == [
        {"role": "user", "content": "my question"},
        {"role": "assistant", "content": "my answer"},
    ]


def test_append_rejects_an_unknown_conversation(repository):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        repository.append("missing", "user", "hello", NOW)


def test_append_rejects_an_unknown_role(repository):
    conversation_id = repository.create("P1001", NOW)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        repository.append(conversation_id, "tool", "{}", NOW)


def test_append_stores_seconds(database, repository):
    conversation_id = repository.create("P1001", NOW)

    repository.append(conversation_id, "user", "hello", NOW)

    with database.connect() as connection:
        row = connection.execute("SELECT created_at FROM messages").fetchone()
    assert row["created_at"] == "2026-10-19T09:00:45"
