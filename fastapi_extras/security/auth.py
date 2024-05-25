import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, Union, runtime_checkable

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from typing_extensions import Annotated

APIKeyScheme = Union[APIKeyCookie, APIKeyHeader, APIKeyQuery]
Authorizer = Callable[[Request, str], Awaitable[Any]]

DEFAULT_SCHEME = APIKeyHeader(name="Authorization")
UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

logger = logging.getLogger(__name__)


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> Optional[str]: ...
    async def set(self, key: str, value: str) -> None: ...


class CacheEntry:
    def __init__(self, key: str, /, *, key_prefix: str = "", backend: Optional[Cache] = None):
        self.key = CacheEntry.keygen(key, key_prefix)
        self.backend = backend
        self.value = None

    async def exists(self) -> bool:
        return await self.get() is not None

    async def get(self) -> Optional[Dict[str, Any]]:
        if self.backend is None:
            return None

        try:
            value = await self.backend.get(self.key)
            if value:
                self.value = json.loads(value)
        except Exception as error:
            logger.error(error)
            self.value = None

        return self.value

    async def set(self, value: Dict[str, Any]):
        if self.backend is None:
            return None

        try:
            data = json.dumps(value)
            await self.backend.set(self.key, data)
        except Exception as error:
            logger.error(error)
            self.value = None

        self.value = value

    @staticmethod
    def keygen(key: str, prefix: str = "") -> str:
        hash = hashlib.sha256(key.encode()).hexdigest()
        return f"{prefix}{hash}"


def remote_authorization(
    url: str,
    *,
    scheme: APIKeyScheme = DEFAULT_SCHEME,
    cache: Optional[Cache] = None,
    **kwargs: Any,
) -> Authorizer:
    assert isinstance(scheme, (APIKeyCookie, APIKeyHeader, APIKeyQuery)), "Invalid APIKeyScheme"
    assert isinstance(cache, (type(None), Cache)), "Invalid Cache"

    async def authorizer(request: Request, token: Annotated[str, Depends(scheme)]) -> Any:
        cached = CacheEntry(token, key_prefix="authorizer:", backend=cache)

        if await cached.exists() and isinstance(cached.value, dict):
            if not cached.value.get("authorized"):
                raise UNAUTHORIZED

            request.scope["authorizer"] = cached.value.get("context")
            return request.scope["authorizer"]

        info = {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "cookies": request.cookies,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=info, **kwargs)

                authorized = response.status_code == status.HTTP_200_OK
                context = response.json()
                result = {"authorized": authorized, "context": context}

                await cached.set(result)

                response.raise_for_status()

                request.scope["authorizer"] = context
                return request.scope["authorizer"]
            except httpx.HTTPError as http_error:
                logger.error(http_error)
                raise UNAUTHORIZED

    return authorizer
