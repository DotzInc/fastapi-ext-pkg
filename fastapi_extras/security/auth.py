import logging
from typing import (
    Any,
    Awaitable,
    Callable,
    Union,
)

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from typing_extensions import Annotated

APIKeyScheme = Union[APIKeyCookie, APIKeyHeader, APIKeyQuery]
Authorizer = Callable[[Request, str], Awaitable[Any]]

DEFAULT_SCHEME = APIKeyHeader(name="Authorization")
UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

logger = logging.getLogger(__name__)


def remote_authorization(
    url: str, scheme: APIKeyScheme = DEFAULT_SCHEME, **kwargs: Any
) -> Authorizer:
    assert isinstance(scheme, (APIKeyCookie, APIKeyHeader, APIKeyQuery)), "Invalid APIKeyScheme"

    async def authorizer(request: Request, _token: Annotated[str, Depends(scheme)]) -> Any:
        # TODO: Add support for caching

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
                response.raise_for_status()

                data = response.json()
                request.scope["authorizer"] = data

                return data
            except httpx.HTTPError as http_error:
                logger.error(http_error)
                raise UNAUTHORIZED

    return authorizer
