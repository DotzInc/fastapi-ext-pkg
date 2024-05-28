from typing import Any, Optional

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pytest import MonkeyPatch
from typing_extensions import Annotated

from fastapi_extras.databases.redis import Redis, RedisManager


class FakeRedis:
    db = {}
    closed = []

    async def get(self, key: str) -> Optional[str]:
        return FakeRedis.db.get(key)

    async def set(
        self, key: str, val: str, ttl: Optional[int] = None, *args: Any, **kwargs: Any
    ) -> None:
        FakeRedis.db[key] = val

    async def aclose(self):
        FakeRedis.closed.append(id(self))

    @classmethod
    def flush(cls):
        cls.db.clear()
        cls.closed.clear()


class Item(BaseModel):
    key: str
    val: str


app = FastAPI()
redis_manager = RedisManager("redis://localhost:6379/0")


@app.get("/items/{key}", response_model=Item)
async def read(key: str, redis: Annotated[Redis, Depends(redis_manager)]):
    item = await redis.get(key) or ""

    if not item:
        HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    return Item(key=key, val=item)


@app.post("/items/", response_model=Item, status_code=status.HTTP_201_CREATED)
async def write(item: Item, redis: Annotated[Redis, Depends(redis_manager)]):
    await redis.set(item.key, item.val)
    return item


client = TestClient(app)


@pytest.fixture(autouse=True)
def redis_mock(monkeypatch: MonkeyPatch):
    monkeypatch.setattr("redis.asyncio.Redis.get", FakeRedis.get)
    monkeypatch.setattr("redis.asyncio.Redis.set", FakeRedis.set)
    monkeypatch.setattr("redis.asyncio.Redis.aclose", FakeRedis.aclose)


@pytest.fixture(autouse=True)
def redis_flush():
    FakeRedis.flush()


def test_redis_manager():
    data = [
        {"key": "foo", "val": "bar"},
        {"key": "bar", "val": "baz"},
        {"key": "baz", "val": "foo"},
    ]

    for item in data:
        response = client.post("/items/", json=item)
        assert response.status_code == 201
        assert response.json() == item

        response = client.get(f"/items/{item['key']}")
        assert response.status_code == 200
        assert response.json() == item

    assert len(set(FakeRedis.closed)) == len(data) * 2
