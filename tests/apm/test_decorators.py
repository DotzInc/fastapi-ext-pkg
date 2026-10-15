import json

import httpx
import pytest
from elasticapm.conf.constants import MASK
from pydantic import BaseModel

from fastapi_extras.apm.constants import EXTERNAL, UNKNOWN_STATUS_CODE, UNREADABLE_RESPONSE
from fastapi_extras.apm.decorators import (
    build_span_labels,
    capture_http_request,
    find_outgoing_request,
    get_span_name,
    read_error,
    read_response,
)
from tests.apm.fakes import AiohttpLikeResponse, BrokenTextResponse, DomainError, SyncTextResponse


class OutgoingRequest(BaseModel):
    method: str
    url: str
    payload: dict = {}


class IncomingRequest:
    method = "GET"
    url = object()


class Repository:
    @capture_http_request
    async def call(self, request: OutgoingRequest, response=None, error=None, **kwargs):
        if error:
            raise error
        return response


def test_get_span_name_keeps_path_and_query():
    assert get_span_name("get", "http://api/v1/users?active=true") == "GET /v1/users?active=true"
    assert get_span_name("post", "https://api/login") == "POST /login"


def test_find_outgoing_request_prefers_kwarg_then_positional():
    positional = OutgoingRequest(method="GET", url="http://a/1")
    keyword = OutgoingRequest(method="POST", url="http://a/2")

    assert find_outgoing_request((IncomingRequest(), positional), {"request": keyword}) is keyword
    assert find_outgoing_request((IncomingRequest(), positional), {}) is positional
    assert find_outgoing_request((IncomingRequest(),), {"request": IncomingRequest()}) is None


@pytest.mark.anyio
async def test_read_response_supports_aiohttp_httpx_and_sync_text():
    assert await read_response(AiohttpLikeResponse(404, "missing")) == (404, "missing")
    assert await read_response(httpx.Response(200, text="ok")) == (200, "ok")
    assert await read_response(SyncTextResponse()) == (201, "created")
    assert await read_response(BrokenTextResponse()) == (200, UNREADABLE_RESPONSE)
    assert await read_response({"not": "a response"}) == (None, None)


def test_read_error_uses_domain_attributes():
    error = DomainError(422, errors=[{"code": "E1"}], content={"errors": []})
    empty = DomainError(503)

    assert read_error(error) == (422, {"errors": [{"code": "E1"}], "content": {"errors": []}})
    assert read_error(empty) == (503, {})
    assert read_error(ValueError("boom")) == (UNKNOWN_STATUS_CODE, "ValueError('boom')")


def test_build_span_labels_masks_by_key_and_serializes(field_names):
    labels = build_span_labels(
        200,
        b'{"access_token": "abc", "name": "Ana"}',
        OutgoingRequest(method="POST", url="http://a", payload={"password": "p"}),
        {"authorization": "Bearer x", "flow": "ep"},
        field_names,
    )

    assert labels["status_code"] == 200
    assert json.loads(labels["response_body"]) == {"access_token": MASK, "name": "Ana"}
    assert json.loads(labels["payload"]) == {
        "method": "POST",
        "url": "http://a",
        "payload": {"password": MASK},
    }
    assert labels["authorization"] == MASK
    assert labels["flow"] == "ep"


@pytest.mark.usefixtures("apm_client")
def test_build_span_labels_uses_client_field_names():
    assert build_span_labels(200, '{"cpf": "1"}', None)["response_body"] == '{"cpf": "[REDACTED]"}'


@pytest.mark.anyio
async def test_capture_http_request_records_successful_call(apm_client):
    request = OutgoingRequest(method="post", url="http://api/v1/login?x=1", payload={"cpf": "1"})
    response = httpx.Response(200, json={"refresh_token": "r", "id": 7})
    apm_client.begin_transaction("request")

    result = await Repository().call(request, response=response, labels={"flow": "login"})

    apm_client.end_transaction("POST /login", "HTTP 2xx")
    [span] = apm_client._transport.events_of("span")
    assert result is response
    assert span["name"] == "POST /v1/login?x=1"
    assert span["type"] == EXTERNAL
    tags = span["context"]["tags"]
    assert tags["status_code"] == 200
    assert json.loads(tags["response_body"]) == {"refresh_token": MASK, "id": 7}
    assert json.loads(tags["payload"]) == {"cpf": MASK}
    assert tags["flow"] == "login"


@pytest.mark.anyio
async def test_capture_http_request_records_domain_error(apm_client):
    request = OutgoingRequest(method="get", url="http://api/v1/users")
    error = DomainError(400, errors={"password": "wrong"})
    apm_client.begin_transaction("request")

    with pytest.raises(DomainError):
        await Repository().call(request=request, error=error)

    apm_client.end_transaction("GET /users", "HTTP 4xx")
    [span] = apm_client._transport.events_of("span")
    assert span["name"] == "GET /v1/users"
    assert span["outcome"] == "failure"
    assert span["context"]["tags"]["status_code"] == 400
    assert json.loads(span["context"]["tags"]["response_body"]) == {"errors": {"password": MASK}}


@pytest.mark.anyio
async def test_capture_http_request_without_request_or_transaction():
    @capture_http_request
    async def call():
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await call()
