from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class Publisher(Protocol):
    def publish(self, recipient: str, message: BaseModel, **attrs: Any) -> Any: ...
