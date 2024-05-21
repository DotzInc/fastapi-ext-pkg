from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from httpx import Request as HTTPXRequest
from httpx import Response as HTTPXResponse
from httpx import codes

from fastapi_extras.security import auth

AUTH_URL = "http://auth.test"
AUTH_KEY = "supers3cr3t"
USER_KEY = "cmFwYWR1cmEK"


@pytest.fixture
def scope() -> Dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/items/",
        "query_string": "",
        "headers": [(b"authorization", USER_KEY.encode())],
    }


@pytest.fixture
def payload(scope) -> Dict[str, Any]:
    return {
        "method": scope["method"],
        "path": scope["path"],
        "query_params": {},
        "headers": {k.decode(): v.decode() for k, v in scope["headers"]},
        "cookies": {},
    }


@pytest.mark.anyio
async def test_remote_authorization(
    scope: Dict[str, Any],
    payload: Dict[str, Any],
    httpx_asyncmock: AsyncMock,
):
    httpx_request = HTTPXRequest("POST", AUTH_URL, json=payload, headers={"x-api-key": AUTH_KEY})
    httpx_response = HTTPXResponse(request=httpx_request, status_code=codes.OK, json={"foo": "bar"})
    httpx_asyncmock.return_value = httpx_response

    authorizer = auth.remote_authorization(url=AUTH_URL, headers={"x-api-key": AUTH_KEY})
    request = Request(scope=scope)
    response = await authorizer(request, USER_KEY)

    assert response == {"foo": "bar"}
    assert request.scope["authorizer"] == response
    assert httpx_asyncmock.call_count == 1
    assert httpx_asyncmock.call_args.args == ("POST", AUTH_URL)
    assert httpx_asyncmock.call_args.kwargs["json"] == payload
    assert httpx_asyncmock.call_args.kwargs["headers"] == {"x-api-key": AUTH_KEY}


@pytest.mark.anyio
async def test_remote_authorization_raises_401_unauthorized(
    scope: Dict[str, Any],
    payload: Dict[str, Any],
    httpx_asyncmock: AsyncMock,
):
    httpx_request = HTTPXRequest("POST", AUTH_URL, json=payload, headers={"x-api-key": AUTH_KEY})
    httpx_response = HTTPXResponse(request=httpx_request, status_code=codes.UNAUTHORIZED, json={})
    httpx_asyncmock.return_value = httpx_response

    authorizer = auth.remote_authorization(url=AUTH_URL, headers={"x-api-key": AUTH_KEY})
    request = Request(scope=scope)

    with pytest.raises(type(auth.UNAUTHORIZED), match=auth.UNAUTHORIZED.detail):
        await authorizer(request, USER_KEY)

    assert httpx_asyncmock.call_count == 1
    assert httpx_asyncmock.call_args.args == ("POST", AUTH_URL)
    assert httpx_asyncmock.call_args.kwargs["json"] == payload
    assert httpx_asyncmock.call_args.kwargs["headers"] == {"x-api-key": AUTH_KEY}
