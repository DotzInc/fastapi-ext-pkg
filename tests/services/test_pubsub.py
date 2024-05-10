import json
from unittest.mock import MagicMock

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from fastapi_utils.services import protocols, pubsub

MAGIC_NUMBER = "42"


class MessageTest(BaseModel):
    content: str


def test_publisher(pubsub_mock: MagicMock):
    publisher = pubsub.Publisher()

    assert isinstance(publisher, protocols.Publisher)

    pubsub_mock.publish.return_value.result.return_value = MAGIC_NUMBER
    topic = "projects/test-project/topics/test-topic"
    message = MessageTest(content="test")
    result = publisher.publish(topic, message)

    assert result == MAGIC_NUMBER

    args = (topic,)
    kwargs = {"data": json.dumps(jsonable_encoder(message)).encode()}

    assert pubsub_mock.publish.call_count == 1
    assert pubsub_mock.publish.call_args == (args, kwargs)
