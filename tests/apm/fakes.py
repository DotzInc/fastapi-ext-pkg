from typing import Any, Dict, List, Tuple

from elasticapm.transport.base import Transport


class CapturingTransport(Transport):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events: List[Tuple[str, Dict[str, Any]]] = []

    def start_thread(self, pid: Any = None) -> None:
        pass

    def queue(self, event_type: str, data: Dict[str, Any], flush: bool = False) -> None:
        processed = self._process_event(event_type, data)
        if processed:
            self.events.append((event_type, processed))

    def events_of(self, event_type: str) -> List[Dict[str, Any]]:
        return [data for kind, data in self.events if kind == event_type]


class AiohttpLikeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class BrokenTextResponse:
    status = 200

    async def text(self) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class SyncTextResponse:
    status_code = 201

    def text(self) -> str:
        return "created"


class DomainError(Exception):
    def __init__(self, status_code: int, errors: Any = None, content: Any = None) -> None:
        super().__init__("domain error")
        self.status_code = status_code
        self.errors = errors
        self.content = content


class Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("cannot render")
