from typing import Any, AsyncGenerator, Optional, Union

import redis.asyncio as redis
from pydantic import RedisDsn


class Redis(redis.Redis):
    async def get(self, key: str, *args: Any, **kwargs: Any) -> Optional[str]:
        return await super().get(key, *args, **kwargs)

    async def set(
        self, key: str, value: str, ttl: Optional[int] = None, *args: Any, **kwargs: Any
    ) -> None:
        await super().set(key, value, ttl, *args, **kwargs)


class RedisManager:
    def __init__(self, url: Union[RedisDsn, str]):
        self.pool = redis.ConnectionPool.from_url(str(url))

    async def __call__(self) -> AsyncGenerator[Redis, None]:
        cli = Redis(connection_pool=self.pool)

        try:
            yield cli
        finally:
            await cli.aclose()
