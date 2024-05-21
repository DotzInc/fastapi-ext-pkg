from typing import Any, Mapping, Optional

import httpx
from httpx import Response


class RemoteAuthorization:
    def __init__(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.kwargs = kwargs

    async def authorize(
        self, data: Optional[Mapping[str, Any]] = None, json: Any = None
    ) -> Response:
        async with httpx.AsyncClient() as client:
            return await client.post(self.url, data=data, json=json, **self.kwargs)
