from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def storage_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    FakeClient = MagicMock()
    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)

    return FakeClient.return_value


@pytest.fixture(autouse=True)
def pubsub_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    FakePublisherClient = MagicMock()
    monkeypatch.setattr("google.cloud.pubsub_v1.PublisherClient", FakePublisherClient)

    return FakePublisherClient.return_value
