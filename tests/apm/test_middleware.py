from typing import Any, Dict, List, Tuple

import httpx
import pytest
from elasticapm.conf.constants import MASK
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pytest import MonkeyPatch

from fastapi_extras.apm.constants import RESPONSE_BODY_CONTEXT_KEY, RESPONSE_BODY_TOO_LARGE
from fastapi_extras.apm.middleware import ElasticAPMMiddleware
from fastapi_extras.middlewares.remove_cloud_trace_headers import RemoveCloudTraceHeadersMiddleware
from fastapi_extras.middlewares.remove_path import RemovePathMiddleware

Header = Tuple[bytes, bytes]
JSON: Header = (b"content-type", b"application/json")

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


def raw_app(headers: List[Header], *chunks: bytes):
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for index, chunk in enumerate(chunks):
            more_body = index < len(chunks) - 1
            await send({"type": "http.response.body", "body": chunk, "more_body": more_body})

    return app


async def run_raw_app(client, headers: List[Header], *chunks: bytes, **options: Any):
    middleware = ElasticAPMMiddleware(raw_app(headers, *chunks), client=client, **options)
    send = SendRecorder()

    await middleware(http_scope("/raw"), make_receive(), send)

    client.end_transaction()
    [transaction] = client._transport.events_of("transaction")
    return transaction, send


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
async def test_records_chunked_response_only_after_last_chunk(apm_client):
    transaction, send = await run_raw_app(apm_client, [JSON], b'{"token": ', b"", b'"t"}')

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {"token": MASK}
    assert [message.get("body") for message in send.messages[1:]] == [b'{"token": ', b"", b'"t"}']


@pytest.mark.anyio
async def test_records_json_media_type_variants(apm_client):
    headers = [(b"content-type", b"Application/Problem+JSON; charset=utf-8")]

    transaction, _send = await run_raw_app(apm_client, headers, b'{"cpf": "123", "title": "x"}')

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {
        "cpf": MASK,
        "title": "x",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-type", b"text/csv")],
        [(b"content-type", b"application/octet-stream")],
        [JSON, (b"content-encoding", b"gzip")],
    ],
)
async def test_skips_bodies_that_cannot_be_sanitized(apm_client, headers):
    transaction, send = await run_raw_app(apm_client, headers, b'{"cpf": "123"}')

    assert "custom" not in transaction["context"]
    assert send.messages[-1]["body"] == b'{"cpf": "123"}'


@pytest.mark.anyio
async def test_records_identity_encoded_json(apm_client):
    headers = [JSON, (b"content-encoding", b"identity")]

    transaction, _send = await run_raw_app(apm_client, headers, b'{"cpf": "123"}')

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {"cpf": MASK}


@pytest.mark.anyio
async def test_omits_body_larger_than_declared_limit(apm_client, monkeypatch: MonkeyPatch):
    joined = []
    monkeypatch.setattr(
        "fastapi_extras.apm.middleware.mask_sensitive_data", lambda *args: joined.append(args)
    )
    headers = [JSON, (b"content-length", b"14")]

    transaction, send = await run_raw_app(
        apm_client, headers, b'{"cpf": "123"}', max_response_body_bytes=10
    )

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == RESPONSE_BODY_TOO_LARGE
    assert joined == []
    assert send.messages[-1]["body"] == b'{"cpf": "123"}'


@pytest.mark.anyio
async def test_omits_streamed_body_that_outgrows_limit(apm_client):
    transaction, send = await run_raw_app(
        apm_client, [JSON], b'{"cpf": ', b'"123"', b"}", max_response_body_bytes=10
    )

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == RESPONSE_BODY_TOO_LARGE
    assert len(send.messages) == 4


@pytest.mark.anyio
async def test_records_body_at_exact_limit(apm_client):
    body = b'{"cpf": "123"}'
    headers = [JSON, (b"content-length", str(len(body)).encode())]

    transaction, _send = await run_raw_app(
        apm_client, headers, body, max_response_body_bytes=len(body)
    )

    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {"cpf": MASK}


@pytest.mark.anyio
async def test_works_inside_service_middleware_stack(make_client):
    client = make_client(CAPTURE_BODY="off")
    service = FastAPI()

    @service.get("/items")
    async def items():
        return {"access_token": "abc", "name": "Ana"}

    service.add_middleware(RemovePathMiddleware, path="/svc")
    service.add_middleware(RemoveCloudTraceHeadersMiddleware)
    service.add_middleware(ElasticAPMMiddleware, client=client)
    service.add_middleware(CORSMiddleware, allow_origins=["*"])

    transport = httpx.ASGITransport(app=service)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/svc/items", headers={"origin": "http://front"})

    client.end_transaction()
    [transaction] = client._transport.events_of("transaction")
    assert response.status_code == 200
    assert response.json() == {"access_token": "abc", "name": "Ana"}
    assert response.headers["access-control-allow-origin"] == "*"
    assert transaction["result"] == "HTTP 2xx"
    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {
        "access_token": MASK,
        "name": "Ana",
    }
