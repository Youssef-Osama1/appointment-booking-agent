import pytest

from app.config.settings import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings():
    return Settings(_env_file=None, llm_api_key="test-key", llm_model="test-model")
