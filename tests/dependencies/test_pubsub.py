import json
from unittest.mock import MagicMock

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from fastapi_utils.dependencies import pubsub
from fastapi_utils.protocols import Publisher

MAGIC_NUMBER = "42"


class MessageTest(BaseModel):
    content: str


@pytest.fixture
def pubsub_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    FakePublisherClient = MagicMock()
    client = FakePublisherClient.return_value
    client.publish.return_value.result.return_value = MAGIC_NUMBER

    monkeypatch.setattr("google.cloud.pubsub_v1.PublisherClient", FakePublisherClient)

    return client


def test_publisher(pubsub_mock: MagicMock):
    publisher = pubsub.Publisher()

    assert isinstance(publisher, Publisher)

    topic = "projects/test-project/topics/test-topic"
    message = MessageTest(content="test")
    result = publisher.publish(topic, message)

    assert result == MAGIC_NUMBER

    args = (topic,)
    kwargs = {"data": json.dumps(jsonable_encoder(message)).encode()}

    assert pubsub_mock.publish.called_once_with(*args, **kwargs)
