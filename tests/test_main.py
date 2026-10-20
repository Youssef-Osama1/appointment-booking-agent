import json

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import AgentStepLimitError, LLMOutputError
from app.main import app, handle_model_failure
from app.services.llm_client import LLMClient


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", [LLMOutputError("refused"), AgentStepLimitError("too many steps")]
)
async def test_model_failures_become_502(error):
    response = await handle_model_failure(None, error)

    assert response.status_code == 502
    assert json.loads(response.body) == {"detail": str(error)}


def test_lifespan_creates_llm_client(monkeypatch, settings):
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with TestClient(app):
        assert isinstance(app.state.llm_client, LLMClient)
