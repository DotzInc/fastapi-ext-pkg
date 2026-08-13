import hashlib
import json
import logging
from typing import Any, AsyncGenerator, Callable, Optional, Protocol, Union

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from pydantic import AnyHttpUrl, RedisDsn
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from typing_extensions import Annotated

logger = logging.getLogger(__name__)
ssl_context = httpx.create_ssl_context()

UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
DEFAULT_SCHEME = APIKeyHeader(name="Authorization")
DEFAULT_CACHE_TTL = 60  # seconds

# Redis client bounds: keep a stuck connection from a Redis outage from being
# reused forever instead of getting discarded and replaced.
DEFAULT_SOCKET_TIMEOUT = 2.0
DEFAULT_SOCKET_CONNECT_TIMEOUT = 2.0
DEFAULT_HEALTH_CHECK_INTERVAL = 30
DEFAULT_HTTP_TIMEOUT = 5.0
DEFAULT_HTTP_CONNECT_TIMEOUT = 2.0

APIKeyScheme = Union[APIKeyCookie, APIKeyHeader, APIKeyQuery]

CacheDependency = Callable[..., Union[Redis, AsyncGenerator[Redis, None]]]
HTTPClientDependency = Callable[..., httpx.AsyncClient]


def keygen(seed: str, prefix: str = "") -> str:
    hash = hashlib.sha256(seed.encode()).hexdigest()
    return f"{prefix}{hash}"


class AsyncRedisCache:
    """FastAPI dependency yielding a redis.asyncio.Redis bound to a shared
    pool with bounded timeouts + health checks, so stuck connections from a
    Redis outage get discarded instead of reused forever."""

    def __init__(
        self,
        url: Union[RedisDsn, str],
        *,
        socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
        socket_connect_timeout: float = DEFAULT_SOCKET_CONNECT_TIMEOUT,
        health_check_interval: int = DEFAULT_HEALTH_CHECK_INTERVAL,
    ) -> None:
        self.pool = ConnectionPool.from_url(
            str(url),
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            health_check_interval=health_check_interval,
            retry_on_error=[RedisConnectionError],
            retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), retries=1),
        )

    async def __call__(self) -> AsyncGenerator[Redis, None]:
        cli = Redis(connection_pool=self.pool)
        try:
            yield cli
        finally:
            await cli.aclose()


class Authorizer(Protocol):
    """Structural type for what remote_authorization_async returns: a FastAPI
    dependency callable that also owns a closeable httpx client."""

    async def __call__(
        self, request: Request, token: str, cache: Redis, http_client: httpx.AsyncClient
    ) -> Any: ...
    async def aclose(self) -> None: ...


def remote_authorization_async(
    url: Union[str, AnyHttpUrl],
    *,
    scheme: APIKeyScheme = DEFAULT_SCHEME,
    cache_gen: CacheDependency,
    http_client_gen: Optional[HTTPClientDependency] = None,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    http_connect_timeout: float = DEFAULT_HTTP_CONNECT_TIMEOUT,
    **kwargs: Any,
) -> Authorizer:
    if not isinstance(scheme, (APIKeyCookie, APIKeyHeader, APIKeyQuery)):
        raise TypeError("Invalid APIKeyScheme")

    request_timeout = httpx.Timeout(http_timeout, connect=http_connect_timeout)

    # Only built when the caller has no httpx client of its own to inject.
    _owned_client: Optional[httpx.AsyncClient] = None

    def _get_owned_client() -> httpx.AsyncClient:
        nonlocal _owned_client
        if _owned_client is None:
            _owned_client = httpx.AsyncClient(verify=ssl_context, timeout=request_timeout)
        return _owned_client

    async def _default_http_client_gen() -> httpx.AsyncClient:
        return _get_owned_client()

    async def aclose() -> None:
        nonlocal _owned_client
        if _owned_client is not None:
            await _owned_client.aclose()
            _owned_client = None

    async def authorizer(
        request: Request,
        token: Annotated[str, Depends(scheme)],
        cache: Annotated[Redis, Depends(cache_gen)],
        http_client: Annotated[
            httpx.AsyncClient, Depends(http_client_gen or _default_http_client_gen)
        ],
    ) -> Any:
        key = keygen(token, prefix="authorizer:")

        cached = None
        try:
            cached = await cache.get(key)
        except RedisError as cache_error:
            logger.warning("Cache get failed, proceeding without cache: %s", cache_error)

        if cached:
            cached = json.loads(cached)

        if cached and isinstance(cached, dict):
            if not cached.get("authorized"):
                raise UNAUTHORIZED

            request.scope["authorizer"] = cached.get("context")
            return request.scope["authorizer"]

        info = {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "cookies": request.cookies,
        }

        # Explicit per-call timeout so it applies even when http_client is an
        # externally-shared client whose own default timeout differs.
        request_kwargs = {**kwargs, "timeout": request_timeout}
        try:
            response = await http_client.post(str(url), json=info, **request_kwargs)
        except httpx.HTTPError as http_error:
            logger.error(http_error)
            raise UNAUTHORIZED
        else:
            authorized = response.status_code == status.HTTP_200_OK
            context = response.json()

            try:
                value = json.dumps({"authorized": authorized, "context": context})
                await cache.set(key, value, ex=cache_ttl)
            except RedisError as cache_error:
                logger.warning("Cache set failed, proceeding without cache: %s", cache_error)

            if not authorized:
                raise UNAUTHORIZED

            request.scope["authorizer"] = context
            return request.scope["authorizer"]

    authorizer.aclose = aclose
    return authorizer
