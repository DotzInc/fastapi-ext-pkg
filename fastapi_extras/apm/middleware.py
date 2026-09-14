import asyncio
import functools
from typing import Any, List, Optional

import elasticapm
from elasticapm.base import Client
from elasticapm.conf import constants
from elasticapm.contrib.asyncio.traces import set_context
from elasticapm.contrib.starlette import ElasticAPM
from elasticapm.contrib.starlette.utils import get_data_from_request, get_data_from_response
from elasticapm.utils.disttracing import TraceParent
from elasticapm.utils.encoding import long_field
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_extras.apm.constants import RESPONSE_BODY_CONTEXT_KEY
from fastapi_extras.apm.masking import mask_sensitive_data


class ElasticAPMMiddleware(ElasticAPM):
    def __init__(
        self,
        app: ASGIApp,
        client: Optional[Client] = None,
        capture_response_body: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(app, client, **kwargs)
        self.capture_response_body = capture_response_body

    def _record_response_body(self, message: Message, chunks: List[bytes]) -> None:
        body = message.get("body", b"")
        if body:
            chunks.append(body)
        if message.get("more_body", False):
            return
        masked_body = mask_sensitive_data(b"".join(chunks), self.client.config.sanitize_field_names)
        elasticapm.set_custom_context({RESPONSE_BODY_CONTEXT_KEY: masked_body})

    async def _read_request_body(self, receive: Receive) -> bytes:
        body = []
        while True:
            message = await receive()
            if not message:
                break
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if chunk:
                    body.append(chunk)
                if not message.get("more_body", False):
                    break
            if message["type"] == "http.disconnect":
                break
        return b"".join(body)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.client.should_ignore_url(scope["path"]):
            await self.app(scope, receive, send)
            return

        response_body_chunks: List[bytes] = []

        @functools.wraps(send)
        async def wrapped_send(message: Message) -> None:
            if message.get("type") == "http.response.start":
                await set_context(
                    lambda: get_data_from_response(
                        message, self.client.config, constants.TRANSACTION
                    ),
                    "response",
                )
                elasticapm.set_transaction_result(
                    "HTTP {}xx".format(message["status"] // 100), override=False
                )
                elasticapm.set_transaction_outcome(
                    http_status_code=message["status"], override=False
                )
            elif message.get("type") == "http.response.body" and self.capture_response_body:
                self._record_response_body(message, response_body_chunks)
            await send(message)

        mocked_receive: Optional[Receive] = None
        request_receive: Optional[Receive] = None

        trace_parent = TraceParent.from_headers(dict(Headers(scope=scope)))
        self.client.begin_transaction("request", trace_parent=trace_parent)

        if self.client.config.capture_body != "off":
            joined_body = await self._read_request_body(receive)

            async def _mocked_receive() -> Message:
                await asyncio.sleep(0)
                return {"type": "http.request", "body": long_field(joined_body)}

            async def _request_receive() -> Message:
                await asyncio.sleep(0)
                return {"type": "http.request", "body": joined_body}

            mocked_receive = _mocked_receive
            request_receive = _request_receive

        request = Request(scope, receive=mocked_receive or receive)
        await self._request_started(request)

        try:
            await self.app(scope, request_receive or receive, wrapped_send)
            elasticapm.set_transaction_outcome(constants.OUTCOME.SUCCESS, override=False)
        except Exception:
            await self.capture_exception(
                context={
                    "request": await get_data_from_request(
                        request, self.client.config, constants.ERROR
                    )
                }
            )
            elasticapm.set_transaction_result("HTTP 5xx", override=False)
            elasticapm.set_transaction_outcome(constants.OUTCOME.FAILURE, override=False)
            elasticapm.set_context({"status_code": 500}, "response")
            raise
