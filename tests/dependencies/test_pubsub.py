import json
from typing import Any, Dict, Tuple

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from fastapi_utils.dependencies import pubsub
from fastapi_utils.protocols import Publisher


class MessageTest(BaseModel):
    content: str


class FakeFuture:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def result(self) -> Tuple[Tuple[Any], Dict[str, Any]]:
        return self.args, self.kwargs


def fake_publish(*args: Any, **kwargs: Any) -> Any:
    return FakeFuture(*args[1:], **kwargs)


@pytest.fixture(autouse=True)
def patch_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pubsub.pubsub_v1.PublisherClient, "publish", fake_publish)


def test_publisher():
    publisher = pubsub.Publisher()

    assert isinstance(publisher, Publisher)

    topic = "projects/test-project/topics/test-topic"
    message = MessageTest(content="test")
    result = publisher.publish(topic, message)
    args = (topic,)
    kwargs = {"data": json.dumps(jsonable_encoder(message)).encode()}

    assert result == (args, kwargs)
