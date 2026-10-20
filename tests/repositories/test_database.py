import sqlite3
import threading

import pytest

from app.repositories.database import Database


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "nested" / "booking.db")
    database.create_schema()
    return database


def insert(connection, patient_id="P1", doctor_id="D1", starts_at="2026-10-22T10:00"):
    connection.execute(
        "INSERT INTO appointments (patient_id, doctor_id, starts_at, status) "
        "VALUES (?, ?, ?, 'booked')",
        (patient_id, doctor_id, starts_at),
    )


def count(database):
    with database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]


def test_create_schema_makes_the_folder_and_tables(tmp_path):
    path = tmp_path / "nested" / "booking.db"

    Database(path).create_schema()

    assert path.is_file()
    with Database(path).connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
    assert {
        "appointments",
        "one_booking_per_doctor_slot",
        "one_booking_per_patient_slot",
        "pending_actions",
        "one_pending_action_per_conversation",
        "conversations",
        "messages",
        "messages_by_conversation",
    } <= tables


def test_create_schema_can_run_twice(database):
    with database.transaction() as connection:
        insert(connection)

    database.create_schema()

    assert count(database) == 1


def test_create_schema_turns_on_wal(database):
    with database.connect() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal"


def test_connect_reads_rows_by_name_with_foreign_keys_on(database):
    with database.connect() as connection:
        row = connection.execute("SELECT 1 AS answer").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert row["answer"] == 1
    assert foreign_keys == 1


def test_connect_closes_the_connection(database):
    with database.connect() as connection:
        pass

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_connect_starts_in_autocommit(database):
    with database.connect() as connection:
        insert(connection)
        assert connection.in_transaction is False

    assert count(database) == 1


def test_transaction_commits_on_success(database):
    with database.transaction() as connection:
        assert connection.in_transaction is True
        insert(connection)

    assert count(database) == 1


def test_transaction_rolls_back_on_error(database):
    with pytest.raises(RuntimeError), database.transaction() as connection:
        insert(connection)
        raise RuntimeError("boom")

    assert count(database) == 0


def test_transaction_rolls_back_every_statement(database):
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        insert(connection)
        insert(connection, patient_id="P2", starts_at="2026-10-22T11:00")
        insert(connection, patient_id="P3")

    assert count(database) == 0


def test_status_must_be_known(database):
    with (
        database.connect() as connection,
        pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"),
    ):
        connection.execute(
            "INSERT INTO appointments (patient_id, doctor_id, starts_at, status) "
            "VALUES ('P1', 'D1', '2026-10-22T10:00', 'pending')"
        )


def test_one_doctor_slot_cannot_be_booked_twice(database):
    with database.connect() as connection:
        insert(connection)
        with pytest.raises(sqlite3.IntegrityError, match="doctor_id"):
            insert(connection, patient_id="P2")


def test_one_patient_cannot_be_in_two_slots_at_once(database):
    with database.connect() as connection:
        insert(connection)
        with pytest.raises(sqlite3.IntegrityError, match="patient_id"):
            insert(connection, doctor_id="D2")


def test_cancelled_rows_do_not_hold_the_slot(database):
    with database.connect() as connection:
        insert(connection)
        connection.execute("UPDATE appointments SET status = 'cancelled'")
        insert(connection, patient_id="P2")
        insert(connection, doctor_id="D2")

    assert count(database) == 3


def test_immediate_transactions_run_one_after_another(database):
    barrier = threading.Barrier(2)
    results = []

    def book(patient_id):
        barrier.wait()
        with database.transaction() as connection:
            taken = connection.execute(
                "SELECT 1 FROM appointments "
                "WHERE doctor_id = 'D1' AND starts_at = '2026-10-22T10:00' "
                "AND status = 'booked'"
            ).fetchone()
            if taken:
                results.append("taken")
                return
            threading.Event().wait(0.05)
            insert(connection, patient_id=patient_id)
            results.append("booked")

    threads = [threading.Thread(target=book, args=(p,)) for p in ("P1", "P2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["booked", "taken"]
    assert count(database) == 1
