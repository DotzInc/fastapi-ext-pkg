import functools
from typing import Any, List, Optional

import elasticapm
from elasticapm.base import Client
from elasticapm.contrib.starlette import ElasticAPM
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_extras.apm.constants import (
    IDENTITY_ENCODING,
    JSON_MEDIA_TYPE,
    JSON_MEDIA_TYPE_SUFFIX,
    MAX_RESPONSE_BODY_BYTES,
    RESPONSE_BODY_CONTEXT_KEY,
    RESPONSE_BODY_TOO_LARGE,
)
from fastapi_extras.apm.masking import mask_sensitive_data


def is_json(headers: Headers) -> bool:
    media_type = headers.get("content-type", "").split(";")[0].strip().lower()
    return media_type == JSON_MEDIA_TYPE or media_type.endswith(JSON_MEDIA_TYPE_SUFFIX)


def is_encoded(headers: Headers) -> bool:
    encoding = headers.get("content-encoding", IDENTITY_ENCODING).strip().lower()
    return encoding not in ("", IDENTITY_ENCODING)


class ResponseBodyRecorder:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.chunks: List[bytes] = []
        self.size = 0
        self.readable = False
        self.too_large = False

    def start(self, message: Message) -> None:
        headers = Headers(raw=message.get("headers", []))
        content_length = headers.get("content-length", "")
        self.readable = is_json(headers) and not is_encoded(headers)
        self.too_large = content_length.isdigit() and int(content_length) > self.max_bytes

    def feed(self, message: Message) -> bool:
        if self.readable and not self.too_large:
            self._append(message.get("body", b""))
        return not message.get("more_body", False)

    def _append(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.max_bytes:
            self.too_large = True
            self.chunks.clear()
        elif chunk:
            self.chunks.append(chunk)


class ElasticAPMMiddleware(ElasticAPM):
    def __init__(
        self,
        app: ASGIApp,
        client: Optional[Client] = None,
        capture_response_body: bool = True,
        max_response_body_bytes: int = MAX_RESPONSE_BODY_BYTES,
        **kwargs: Any,
    ) -> None:
        super().__init__(app, client, **kwargs)
        self.capture_response_body = capture_response_body
        self.max_response_body_bytes = max_response_body_bytes

    def _record_response_body(self, recorder: ResponseBodyRecorder) -> None:
        if not recorder.readable:
            return
        if recorder.too_large:
            body = RESPONSE_BODY_TOO_LARGE
        else:
            body = mask_sensitive_data(
                b"".join(recorder.chunks), self.client.config.sanitize_field_names
            )
        elasticapm.set_custom_context({RESPONSE_BODY_CONTEXT_KEY: body})

    def _should_record(self, scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and self.capture_response_body
            and not self.client.should_ignore_url(scope["path"])
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._should_record(scope):
            await super().__call__(scope, receive, send)
            return

        recorder = ResponseBodyRecorder(self.max_response_body_bytes)

        @functools.wraps(send)
        async def recording_send(message: Message) -> None:
            if message.get("type") == "http.response.start":
                recorder.start(message)
            elif message.get("type") == "http.response.body" and recorder.feed(message):
                self._record_response_body(recorder)
            await send(message)

        await super().__call__(scope, receive, recording_send)
