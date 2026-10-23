import pytest

from app.mcp_server import __main__ as entry
from app.repositories.database import Database


@pytest.fixture
def started(monkeypatch, settings):
    calls = {}

    class FakeServer:
        def run(self, transport):
            calls["transport"] = transport

    def fake_build(clinic, database, settings, patient, conversation_id):
        calls["patient"] = patient.patient_id
        calls["conversation_id"] = conversation_id
        return FakeServer()

    def fake_confirm(clinic, database, settings, patient):
        calls["confirm"] = patient.patient_id
        return 0

    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)
    monkeypatch.setattr(entry, "build_server", fake_build)
    monkeypatch.setattr(entry, "confirm_in_terminal", fake_confirm)
    return calls


@pytest.fixture
def storage(tmp_path, settings):
    settings.database_path = tmp_path / "booking.db"
    return settings


@pytest.fixture
def rows_in():
    def read(settings, sql):
        with Database(settings.database_path).connect() as connection:
            return [tuple(row) for row in connection.execute(sql)]

    return read


def test_serves_the_configured_patient_over_stdio(
    monkeypatch, started, storage, rows_in
):
    monkeypatch.setattr("sys.argv", ["app.mcp_server"])

    entry.main()

    assert started["transport"] == "stdio"
    assert started["patient"] == "P1001"
    assert rows_in(storage, "SELECT patient_id FROM conversations") == [("P1001",)]
    assert rows_in(storage, "SELECT conversation_id FROM conversations") == [
        (started["conversation_id"],)
    ]


def test_confirm_runs_the_terminal_approval(monkeypatch, started, storage, rows_in):
    monkeypatch.setattr("sys.argv", ["app.mcp_server", "confirm"])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == 0
    assert started == {"confirm": "P1001"}
    assert rows_in(storage, "SELECT COUNT(*) FROM conversations") == [(0,)]


@pytest.mark.parametrize("patient_id", [None, "", "P9999"])
def test_unknown_patient_stops_before_serving(
    monkeypatch, started, storage, patient_id
):
    storage.mcp_patient_id = patient_id
    monkeypatch.setattr("sys.argv", ["app.mcp_server"])

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert "Set MCP_PATIENT_ID to a known patient" in str(exit_info.value.code)
    assert started == {}
