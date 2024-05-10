from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class Publisher(Protocol):
    def publish(self, recipient: str, message: BaseModel, **attrs: Any) -> Any: ...


@runtime_checkable
class Uploader(Protocol):
    def upload(self, bucket_name: str, destination_filename: str, source_filename: str) -> None: ...
