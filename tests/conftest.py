from unittest.mock import AsyncMock

import pytest
from pytest import MonkeyPatch


@pytest.fixture
def httpx_asyncmock(monkeypatch: MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.request", mock)

    return mock
