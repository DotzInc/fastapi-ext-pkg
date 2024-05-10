import json
from typing import Any

from fastapi.encoders import jsonable_encoder
from google.cloud import pubsub_v1
from pydantic import BaseModel


class Publisher:
    def __init__(self) -> None:
        self.client = pubsub_v1.PublisherClient()

    def publish(self, recipient: str, message: BaseModel, **attrs: Any) -> Any:
        data = jsonable_encoder(message)
        future = self.client.publish(recipient, data=json.dumps(data).encode(), **attrs)

        return future.result()
