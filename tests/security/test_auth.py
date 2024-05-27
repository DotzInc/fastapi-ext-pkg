from typing import Any, AsyncGenerator, Callable, Dict, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.security import APIKeyQuery
from fastapi.testclient import TestClient
from httpx import ConnectTimeout, Request, Response, codes
from pydantic import BaseModel
from pytest import MonkeyPatch
from typing_extensions import Annotated

from fastapi_extras.security import auth

AUTH_URL = "http://auth.test"
AUTH_KEY = "supers3cr3t"
USER_KEY = "cmFwYWR1cmEK"


class FakeCache:
    def __init__(self):
        self.db = {}
        self.hits = 0

    async def get(self, key: str) -> Optional[str]:
        self.hits += 1 if key in self.db else 0
        return self.db.get(key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None):
        self.db[key] = value

    async def aclose(self):
        pass

    def flush(self):
        self.db.clear()
        self.hits = 0


class FakeCacheGenerator:
    def __init__(self):
        self.cache = FakeCache()

    async def __call__(self) -> AsyncGenerator[FakeCache, None]:
        try:
            yield self.cache
        finally:
            await self.cache.aclose()


query_scheme = APIKeyQuery(name="token")
cache_gen = FakeCacheGenerator()

authorizer = auth.remote_authorization(
    AUTH_URL,
    scheme=query_scheme,
    headers={"x-api-key": AUTH_KEY},
)

cached_authorizer = auth.remote_authorization(
    AUTH_URL,
    cache_gen=cache_gen,
    headers={"x-api-key": AUTH_KEY},
)

appy = FastAPI(dependencies=[Depends(authorizer)])
appz = FastAPI(dependencies=[Depends(cached_authorizer)])


class Message(BaseModel):
    content: str


@appy.post("/echo/")
async def echo(message: Message):
    return {"message": message.content}


@appz.get("/health/", status_code=status.HTTP_204_NO_CONTENT)
def health_check():
    pass


@appz.get("/auth-info/")
async def auth_info(info: Annotated[Dict[str, Any], Depends(cached_authorizer)]):
    return info


appy_client = TestClient(appy)
appz_client = TestClient(appz)


@pytest.fixture(autouse=True)
def flush_cache():
    cache_gen.cache.flush()


@pytest.mark.fixture
def httpx_asyncmock(monkeypatch: MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.request", mock)

    return mock


@pytest.fixture
def authorized() -> Callable[..., Response]:
    def builder(
        method: str = "GET",
        path: str = "/",
        query_params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, Any]] = None,
        is_authorized: bool = True,
    ):
        query_params = query_params or {}
        headers = headers or {}
        cookies = cookies or {}
        status_code = codes.UNAUTHORIZED
        content = {"message": "Invalid credentials"}

        if is_authorized:
            status_code = codes.OK
            content = {"sid": "d4ad3d03-1cbe-40a2-8002-e060a65fede0"}

        request = Request(
            "POST",
            AUTH_URL,
            headers={"x-api-key": AUTH_KEY, "content-type": "application/json"},
            json={
                "method": method,
                "path": path,
                "query_params": query_params,
                "headers": headers,
                "cookies": cookies,
            },
        )

        response = Response(
            request=request,
            status_code=status_code,
            headers={"content-type": "application/json"},
            json=content,
        )

        return response

    return builder


def test_echo(httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]):
    headers = {"content-type": "application/json"}
    query_params = {"token": USER_KEY}
    path = "/echo/"
    message = {"content": "echo"}

    httpx_asyncmock.return_value = authorized(
        method="POST",
        path=path,
        query_params=query_params,
        headers=headers,
    )

    response = appy_client.post(path, json=message, params=query_params, headers=query_params)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": message["content"]}
    assert httpx_asyncmock.await_count == 1

    response = appy_client.post(path, json=message, params=query_params, headers=query_params)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": message["content"]}
    assert httpx_asyncmock.await_count == 2


def test_echo_without_required_credential(httpx_asyncmock: AsyncMock):
    headers = {"content-type": "application/json"}
    path = "/echo/"
    message = {"content": "echo"}

    response = appy_client.post(path, json=message, headers=headers)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json() == {"detail": "Not authenticated"}
    assert httpx_asyncmock.await_count == 0


def test_health_check(httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 0

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 1


def test_auth_info(httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/auth-info/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"sid": "d4ad3d03-1cbe-40a2-8002-e060a65fede0"}
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 0

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"sid": "d4ad3d03-1cbe-40a2-8002-e060a65fede0"}
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 1


def test_health_check_with_invalid_credentials(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]
):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers, is_authorized=False)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 0

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 1


def test_health_check_when_authorization_request_fails(httpx_asyncmock: AsyncMock):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.side_effect = ConnectTimeout("Connection timed out")

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1
    assert cache_gen.cache.hits == 0

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 2
    assert cache_gen.cache.hits == 0
