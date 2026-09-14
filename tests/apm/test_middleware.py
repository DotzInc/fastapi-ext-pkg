from typing import Any, Dict, List

import pytest
from elasticapm.conf.constants import MASK
from fastapi import FastAPI
from pytest import MonkeyPatch

from fastapi_extras.apm.constants import RESPONSE_BODY_CONTEXT_KEY
from fastapi_extras.apm.middleware import ElasticAPMMiddleware

api = FastAPI()


@api.post("/login")
async def login(payload: Dict[str, Any]):
    return {"access_token": "abc", "user": {"cpf": payload.get("cpf"), "name": "Ana"}}


@api.get("/boom")
async def boom():
    raise RuntimeError("boom")


def http_scope(path: str, method: str = "GET") -> Dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
        "app": api,
    }


def make_receive(*messages: Any):
    pending = list(messages)

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    return receive


class SendRecorder:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def __call__(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)


async def call_login(middleware: ElasticAPMMiddleware, send: SendRecorder) -> None:
    body = b'{"password": "secret", "cpf": "123"}'
    await middleware(
        http_scope("/login", "POST"),
        make_receive({"type": "http.request", "body": body, "more_body": False}),
        send,
    )


@pytest.mark.anyio
async def test_records_masked_request_and_response_bodies(apm_client):
    middleware = ElasticAPMMiddleware(api, client=apm_client)
    send = SendRecorder()

    await call_login(middleware, send)

    apm_client.end_transaction()
    [transaction] = apm_client._transport.events_of("transaction")
    assert transaction["name"] == "POST /login"
    assert transaction["result"] == "HTTP 2xx"
    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {
        "access_token": MASK,
        "user": {"cpf": MASK, "name": "Ana"},
    }
    assert '"password": "[REDACTED]"' in transaction["context"]["request"]["body"]
    assert send.messages[-1]["body"] == b'{"access_token":"abc","user":{"cpf":"123","name":"Ana"}}'


@pytest.mark.anyio
async def test_skips_response_body_when_disabled(make_client):
    client = make_client(CAPTURE_BODY="off")
    middleware = ElasticAPMMiddleware(api, client=client, capture_response_body=False)

    await call_login(middleware, SendRecorder())

    client.end_transaction()
    [transaction] = client._transport.events_of("transaction")
    assert "custom" not in transaction["context"]
    assert transaction["context"]["request"]["body"] == "[REDACTED]"


@pytest.mark.anyio
async def test_captures_exceptions_and_reraises(apm_client):
    middleware = ElasticAPMMiddleware(api, client=apm_client)

    with pytest.raises(RuntimeError):
        await middleware(http_scope("/boom"), make_receive(), SendRecorder())

    apm_client.end_transaction()
    [error] = apm_client._transport.events_of("error")
    [transaction] = apm_client._transport.events_of("transaction")
    assert error["exception"]["message"] == "RuntimeError: boom"
    assert transaction["result"] == "HTTP 5xx"
    assert transaction["outcome"] == "failure"


@pytest.mark.anyio
async def test_passes_through_non_http_and_ignored_urls(apm_client, monkeypatch: MonkeyPatch):
    calls = []

    async def app(scope, _receive, _send):
        calls.append(scope["type"])

    middleware = ElasticAPMMiddleware(app, client=apm_client)
    monkeypatch.setattr(apm_client, "should_ignore_url", lambda path: path == "/health")

    await middleware({"type": "lifespan"}, make_receive(), SendRecorder())
    await middleware(http_scope("/health"), make_receive(), SendRecorder())

    assert calls == ["lifespan", "http"]
    assert apm_client._transport.events == []


@pytest.mark.anyio
async def test_read_request_body_handles_chunks_and_interruptions(apm_client):
    middleware = ElasticAPMMiddleware(api, client=apm_client)

    chunked = make_receive(
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": False},
    )
    interrupted = make_receive({"type": "http.request", "body": b"ab", "more_body": True}, None)
    disconnected = make_receive({"type": "http.disconnect"})

    assert await middleware._read_request_body(chunked) == b"abcd"
    assert await middleware._read_request_body(interrupted) == b"ab"
    assert await middleware._read_request_body(disconnected) == b""


def test_record_response_body_waits_for_last_chunk(apm_client, monkeypatch: MonkeyPatch):
    contexts = []
    monkeypatch.setattr(
        "fastapi_extras.apm.middleware.elasticapm.set_custom_context", contexts.append
    )
    middleware = ElasticAPMMiddleware(api, client=apm_client)
    chunks: List[bytes] = []

    middleware._record_response_body({"body": b'{"token": ', "more_body": True}, chunks)
    assert contexts == []
    middleware._record_response_body({"body": b'"t"}'}, chunks)

    assert contexts == [{RESPONSE_BODY_CONTEXT_KEY: {"token": MASK}}]
