import hashlib
import json
import logging
import typing
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional, Protocol, Union

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from pydantic import AnyHttpUrl
from typing_extensions import Annotated

logger = logging.getLogger(__name__)
ssl_context = httpx.create_ssl_context()

UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
DEFAULT_SCHEME = APIKeyHeader(name="Authorization")
DEFAULT_CACHE_TTL = 60  # seconds


def DEFAULT_CACHE_GEN() -> None:
    pass


@typing.runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> Optional[str]: ...
    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None: ...


APIKeyScheme = Union[APIKeyCookie, APIKeyHeader, APIKeyQuery]
Authorizer = Callable[[Request, str, Optional[Cache]], Awaitable[Any]]
CacheGenerator = Callable[..., Union[Cache, AsyncGenerator[Cache, None], None]]


def keygen(seed: str, prefix: str = "") -> str:
    hash = hashlib.sha256(seed.encode()).hexdigest()
    return f"{prefix}{hash}"


def remote_authorization(
    url: Union[str, AnyHttpUrl],
    *,
    scheme: APIKeyScheme = DEFAULT_SCHEME,
    cache_gen: CacheGenerator = DEFAULT_CACHE_GEN,
    cache_ttl: int = DEFAULT_CACHE_TTL,
    **kwargs: Any,
) -> Authorizer:
    assert isinstance(scheme, (APIKeyCookie, APIKeyHeader, APIKeyQuery)), "Invalid APIKeyScheme"
    # TODO: validate cachegen argument using introspection and type annotations

    async def authorizer(
        request: Request,
        token: Annotated[str, Depends(scheme)],
        cache: Annotated[Optional[Cache], Depends(cache_gen)],
    ) -> Any:
        key = keygen(token, prefix="authorizer:")
        cached = await cache.get(key) if cache else None

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

        async with httpx.AsyncClient(verify=ssl_context) as client:
            try:
                response = await client.post(str(url), json=info, **kwargs)
            except httpx.HTTPError as http_error:
                logger.error(http_error)
                raise UNAUTHORIZED
            else:
                authorized = response.status_code == status.HTTP_200_OK
                context = response.json()

                if cache:
                    value = json.dumps({"authorized": authorized, "context": context})
                    await cache.set(key, value, cache_ttl)

                if not authorized:
                    raise UNAUTHORIZED

                request.scope["authorizer"] = context
                return request.scope["authorizer"]

    return authorizer
