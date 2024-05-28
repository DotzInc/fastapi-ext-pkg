from typing import AsyncGenerator, Union

import redis.asyncio as redis
from pydantic import RedisDsn


class RedisManager:
    def __init__(self, url: Union[RedisDsn, str]):
        self.pool = redis.ConnectionPool.from_url(str(url))

    async def __call__(self) -> AsyncGenerator[redis.Redis, None]:
        cli = redis.Redis(connection_pool=self.pool)

        try:
            yield cli
        finally:
            await cli.aclose()
