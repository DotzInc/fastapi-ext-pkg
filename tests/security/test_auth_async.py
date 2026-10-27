import asyncio
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.security import APIKeyQuery
from fastapi.testclient import TestClient
from httpx import AsyncClient, ConnectTimeout, Request, Response, codes
from pydantic import BaseModel
from pytest import MonkeyPatch
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from typing_extensions import Annotated

from fastapi_extras.security import auth_async

AUTH_URL = "http://auth.test"
AUTH_KEY = "supers3cr3t"
USER_KEY = "cmFwYWR1cmEK"

broken_get_pools = set()
broken_set_pools = set()


class FakeRedis:
    db: Dict[str, str] = {}
    closed = []

    async def get(self, key: str) -> Optional[str]:
        if id(self.connection_pool) in broken_get_pools:
            raise RedisConnectionError("Redis is offline")
        return FakeRedis.db.get(key)

    async def set(self, key: str, value: str, *args: Any, **kwargs: Any) -> None:
        if id(self.connection_pool) in broken_set_pools:
            raise RedisConnectionError("Redis is offline")
        FakeRedis.db[key] = value

    async def aclose(self):
        FakeRedis.closed.append(id(self))

    @classmethod
    def flush(cls):
        cls.db.clear()
        cls.closed.clear()


@pytest.fixture(autouse=True)
def redis_mock(monkeypatch: MonkeyPatch):
    monkeypatch.setattr("redis.asyncio.Redis.get", FakeRedis.get)
    monkeypatch.setattr("redis.asyncio.Redis.set", FakeRedis.set)
    monkeypatch.setattr("redis.asyncio.Redis.aclose", FakeRedis.aclose)


@pytest.fixture(autouse=True)
def redis_flush():
    FakeRedis.flush()


query_scheme = APIKeyQuery(name="token")

echo_cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")
cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")
broken_get_cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")
broken_set_cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")

broken_get_pools.add(id(broken_get_cache.pool))
broken_set_pools.add(id(broken_set_cache.pool))

authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    scheme=query_scheme,
    cache_gen=echo_cache,
    headers={"x-api-key": AUTH_KEY},
)

cached_authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    cache_gen=cache,
    headers={"x-api-key": AUTH_KEY},
)

broken_get_authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    cache_gen=broken_get_cache,
    headers={"x-api-key": AUTH_KEY},
)

broken_set_authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    cache_gen=broken_set_cache,
    headers={"x-api-key": AUTH_KEY},
)

appy = FastAPI(dependencies=[Depends(authorizer)])
appz = FastAPI(dependencies=[Depends(cached_authorizer)])
app_broken_get = FastAPI(dependencies=[Depends(broken_get_authorizer)])
app_broken_set = FastAPI(dependencies=[Depends(broken_set_authorizer)])


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


@app_broken_get.get("/health/", status_code=status.HTTP_204_NO_CONTENT)
def health_check_broken_get():
    pass


@app_broken_set.get("/health/", status_code=status.HTTP_204_NO_CONTENT)
def health_check_broken_set():
    pass


appy_client = TestClient(appy)
appz_client = TestClient(appz)
app_broken_get_client = TestClient(app_broken_get)
app_broken_set_client = TestClient(app_broken_set)


@pytest.fixture
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


def test_remote_authorization_async_rejects_invalid_scheme():
    with pytest.raises(TypeError):
        auth_async.remote_authorization_async(AUTH_URL, scheme=object(), cache_gen=echo_cache)


def test_async_redis_cache_configures_pool_with_bounded_timeouts():
    redis_cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")

    kwargs = redis_cache.pool.connection_kwargs

    assert kwargs["socket_timeout"] == auth_async.DEFAULT_SOCKET_TIMEOUT
    assert kwargs["socket_connect_timeout"] == auth_async.DEFAULT_SOCKET_CONNECT_TIMEOUT
    assert kwargs["health_check_interval"] == auth_async.DEFAULT_HEALTH_CHECK_INTERVAL

    custom_cache = auth_async.AsyncRedisCache(
        "redis://localhost:6379/0",
        socket_timeout=1.0,
        socket_connect_timeout=1.5,
        health_check_interval=10,
    )

    custom_kwargs = custom_cache.pool.connection_kwargs

    assert custom_kwargs["socket_timeout"] == 1.0
    assert custom_kwargs["socket_connect_timeout"] == 1.5
    assert custom_kwargs["health_check_interval"] == 10


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

    # cache_gen is mandatory in the async authorizer, so the second call with
    # the same token is a cache hit and never reaches the auth backend again.
    response = appy_client.post(path, json=message, params=query_params, headers=query_params)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": message["content"]}
    assert httpx_asyncmock.await_count == 1


def test_echo_without_required_credential(httpx_asyncmock: AsyncMock):
    headers = {"content-type": "application/json"}
    path = "/echo/"
    message = {"content": "echo"}

    response = appy_client.post(path, json=message, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": "Not authenticated"}
    assert httpx_asyncmock.await_count == 0


def test_health_check(httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1
    assert len(FakeRedis.closed) == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1
    # AsyncRedisCache hands out a fresh Redis wrapper per request and closes
    # it in a finally, even on a cache hit.
    assert len(FakeRedis.closed) == 2


def test_auth_info(httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/auth-info/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"sid": "d4ad3d03-1cbe-40a2-8002-e060a65fede0"}
    assert httpx_asyncmock.await_count == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"sid": "d4ad3d03-1cbe-40a2-8002-e060a65fede0"}
    assert httpx_asyncmock.await_count == 1


def test_health_check_with_invalid_credentials(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]
):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers, is_authorized=False)

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1


def test_health_check_when_authorization_request_fails(httpx_asyncmock: AsyncMock):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.side_effect = ConnectTimeout("Connection timed out")

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert httpx_asyncmock.await_count == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert httpx_asyncmock.await_count == 2


def upstream_response(status_code: int, content: bytes, content_type: str) -> Response:
    return Response(
        request=Request("POST", AUTH_URL),
        status_code=status_code,
        headers={"content-type": content_type},
        content=content,
    )


@pytest.mark.parametrize(
    "status_code",
    [codes.INTERNAL_SERVER_ERROR, codes.BAD_GATEWAY, codes.SERVICE_UNAVAILABLE, codes.NOT_FOUND],
)
def test_health_check_when_authorization_service_fails(
    httpx_asyncmock: AsyncMock, status_code: int
):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.return_value = upstream_response(
        status_code, b'{"message": "Internal Server Error"}', "application/json"
    )

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"detail": "Authorization service unavailable"}
    assert httpx_asyncmock.await_count == 1

    # Authorizer failures are not cached, so the next call asks it again.
    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert httpx_asyncmock.await_count == 2


def test_health_check_when_authorization_service_returns_non_json(httpx_asyncmock: AsyncMock):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.return_value = upstream_response(
        codes.OK, b"<html>Bad Gateway</html>", "text/html"
    )

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert httpx_asyncmock.await_count == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert httpx_asyncmock.await_count == 2


@pytest.mark.parametrize("status_code", [codes.BAD_REQUEST, codes.FORBIDDEN])
def test_health_check_when_credentials_are_denied(httpx_asyncmock: AsyncMock, status_code: int):
    headers = {"authorization": "Bearer xoxo"}
    path = "/health/"

    httpx_asyncmock.return_value = upstream_response(
        status_code, b'{"message": "Forbidden"}', "application/json"
    )

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1

    response = appz_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert httpx_asyncmock.await_count == 1


def test_health_check_when_cache_get_fails(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]
):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = app_broken_get_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1

    response = app_broken_get_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 2


def test_health_check_when_cache_set_fails(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]
):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    response = app_broken_set_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 1

    response = app_broken_set_client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert httpx_asyncmock.await_count == 2


closable_cache = auth_async.AsyncRedisCache("redis://localhost:6379/0")
closable_authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    cache_gen=closable_cache,
    headers={"x-api-key": AUTH_KEY},
)


@asynccontextmanager
async def closable_lifespan(_app: FastAPI):
    yield
    await closable_authorizer.aclose()


app_closable = FastAPI(lifespan=closable_lifespan, dependencies=[Depends(closable_authorizer)])


@app_closable.get("/health/", status_code=status.HTTP_204_NO_CONTENT)
def health_check_closable():
    pass


def test_authorizer_http_client_is_reused_and_closed_on_shutdown(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response], monkeypatch: MonkeyPatch
):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    original_init = AsyncClient.__init__
    init_calls = []

    def counting_init(self, *args, **kwargs):
        init_calls.append((args, kwargs))
        return original_init(self, *args, **kwargs)

    close_mock = AsyncMock()
    monkeypatch.setattr(AsyncClient, "__init__", counting_init)
    monkeypatch.setattr(AsyncClient, "aclose", close_mock)

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    with TestClient(app_closable) as client:
        response = client.get(path, headers=headers)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        response = client.get(path, headers=headers)
        assert response.status_code == status.HTTP_204_NO_CONTENT

    # The owned client is lazily built once and reused across both requests,
    # then torn down exactly once when the app (and its lifespan) shut down.
    assert len(init_calls) == 1
    assert close_mock.await_count == 1


external_redis_pool = ConnectionPool.from_url("redis://localhost:6379/0")
external_http_client = AsyncClient(verify=auth_async.ssl_context)


@pytest.fixture(scope="module", autouse=True)
def _close_external_http_client():
    yield
    asyncio.run(external_http_client.aclose())


async def get_external_redis():
    yield Redis(connection_pool=external_redis_pool)


async def get_external_http_client() -> AsyncClient:
    return external_http_client


external_authorizer = auth_async.remote_authorization_async(
    AUTH_URL,
    cache_gen=get_external_redis,
    http_client_gen=get_external_http_client,
    headers={"x-api-key": AUTH_KEY},
)


@asynccontextmanager
async def external_lifespan(_app: FastAPI):
    yield
    await external_authorizer.aclose()


app_external = FastAPI(lifespan=external_lifespan, dependencies=[Depends(external_authorizer)])


@app_external.get("/health/", status_code=status.HTTP_204_NO_CONTENT)
def health_check_external():
    pass


def test_authorizer_accepts_externally_owned_redis_and_http_client(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response], monkeypatch: MonkeyPatch
):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    close_mock = AsyncMock()
    monkeypatch.setattr(AsyncClient, "aclose", close_mock)

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    with TestClient(app_external) as client:
        response = client.get(path, headers=headers)
        assert response.status_code == status.HTTP_204_NO_CONTENT

    # aclose() only tears down clients the authorizer owns itself; an
    # externally-managed client is the caller's responsibility to close.
    assert close_mock.await_count == 0


def test_authorizer_applies_explicit_timeout_even_on_shared_http_client(
    httpx_asyncmock: AsyncMock, authorized: Callable[..., Response]
):
    headers = {"authorization": f"Bearer {USER_KEY}"}
    path = "/health/"

    httpx_asyncmock.return_value = authorized(path=path, headers=headers)

    with TestClient(app_external) as client:
        response = client.get(path, headers=headers)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    _, call_kwargs = httpx_asyncmock.call_args
    assert call_kwargs["timeout"] == auth_async.httpx.Timeout(
        auth_async.DEFAULT_HTTP_TIMEOUT, connect=auth_async.DEFAULT_HTTP_CONNECT_TIMEOUT
    )
