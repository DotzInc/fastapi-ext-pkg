import hashlib
import json
import logging
import typing
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional, Protocol, Tuple, Union

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from pydantic import AnyHttpUrl
from typing_extensions import Annotated

logger = logging.getLogger(__name__)
ssl_context = httpx.create_ssl_context()

UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
SERVICE_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authorization service unavailable"
)
DENIED_STATUS_CODES = frozenset(
    {status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
)
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


def parse_response(response: httpx.Response) -> Tuple[bool, Any]:
    if response.status_code in DENIED_STATUS_CODES:
        return False, None

    if response.status_code != status.HTTP_200_OK:
        logger.error(f"Authorization service returned unexpected status {response.status_code}")
        raise SERVICE_UNAVAILABLE

    try:
        return True, response.json()
    except ValueError:
        logger.error(f"Authorization service returned a non-JSON body: {response.text}")
        raise SERVICE_UNAVAILABLE


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

        cached = None
        try:
            cached = await cache.get(key)
        except Exception as cache_error:
            logger.warning(f"Cache get failed, proceeding without cache: {cache_error}")

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
                logger.error(
                    f"Authorization request failed: {type(http_error).__name__}: {http_error}"
                )
                raise SERVICE_UNAVAILABLE

        authorized, context = parse_response(response)

        try:
            value = json.dumps({"authorized": authorized, "context": context})
            await cache.set(key, value, cache_ttl)
        except Exception as cache_error:
            logger.warning(f"Cache set failed, proceeding without cache: {cache_error}")

        if not authorized:
            raise UNAUTHORIZED

        request.scope["authorizer"] = context
        return request.scope["authorizer"]

    return authorizer
