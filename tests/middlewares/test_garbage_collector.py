import asyncio
import logging
import os
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import LogCaptureFixture, MonkeyPatch

from fastapi_extras.middlewares import garbage_collector
from fastapi_extras.middlewares.garbage_collector import (
    MEGABYTE,
    GarbageCollectorMiddleware,
    load_malloc_trim,
    read_rss_bytes,
    to_megabytes,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_gc(monkeypatch: MonkeyPatch) -> Mock:
    fake = Mock()
    fake.collect.return_value = 7
    fake.get_freeze_count.return_value = 42
    monkeypatch.setattr(garbage_collector, "gc", fake)
    return fake


async def ok_app(_scope: Dict[str, Any], _receive: Any, _send: Any) -> None:
    return None


async def failing_app(_scope: Dict[str, Any], _receive: Any, _send: Any) -> None:
    raise RuntimeError("boom")


def http_scope() -> Dict[str, Any]:
    return {"type": "http", "path": "/", "method": "GET"}


async def noop_receive() -> Dict[str, Any]:
    return {}


async def noop_send(_message: Dict[str, Any]) -> None:
    return None


def build_app(**options: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(GarbageCollectorMiddleware, **options)

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    return app


def test_defaults():
    middleware = GarbageCollectorMiddleware(ok_app)

    assert middleware.every_requests == 5000
    assert middleware.min_interval_seconds == 60.0
    assert middleware.generation == 2
    assert middleware.max_rss_bytes is None
    assert 0 <= middleware.jitter_seconds <= 12


def test_freezes_startup_objects_after_lifespan_startup(fake_gc: Mock, caplog: LogCaptureFixture):
    with caplog.at_level(logging.INFO), TestClient(build_app()) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    fake_gc.collect.assert_called_once_with()
    fake_gc.freeze.assert_called_once_with()
    assert "froze 42 startup objects" in caplog.text


def test_does_not_freeze_when_disabled(fake_gc: Mock):
    with TestClient(build_app(freeze_on_startup=False)) as client:
        client.get("/ping")

    fake_gc.freeze.assert_not_called()


@pytest.mark.anyio
async def test_collects_after_every_requests(fake_gc: Mock, caplog: LogCaptureFixture):
    middleware = GarbageCollectorMiddleware(ok_app, every_requests=2, min_interval_seconds=0)
    middleware.malloc_trim = Mock()

    await middleware(http_scope(), noop_receive, noop_send)
    await asyncio.sleep(0)
    fake_gc.collect.assert_not_called()

    with caplog.at_level(logging.INFO):
        await middleware(http_scope(), noop_receive, noop_send)
        assert middleware.scheduled is True
        await asyncio.sleep(0)

    fake_gc.collect.assert_called_once_with(2)
    middleware.malloc_trim.assert_called_once_with(0)
    assert middleware.requests == 0
    assert middleware.scheduled is False
    assert "Garbage collection generation=2 collected=7" in caplog.text


@pytest.mark.anyio
async def test_collects_when_rss_exceeds_limit(
    monkeypatch: MonkeyPatch, fake_gc: Mock, caplog: LogCaptureFixture
):
    monkeypatch.setattr(garbage_collector, "read_rss_bytes", lambda: 150 * MEGABYTE)
    middleware = GarbageCollectorMiddleware(ok_app, max_rss_mb=100, min_interval_seconds=0)
    middleware.malloc_trim = Mock()

    with caplog.at_level(logging.INFO):
        await middleware(http_scope(), noop_receive, noop_send)
        await asyncio.sleep(0)

    fake_gc.collect.assert_called_once_with(2)
    middleware.malloc_trim.assert_called_once_with(0)
    assert middleware.requests == 0
    assert "rss_before_mb=150.00 rss_after_mb=150.00" in caplog.text


@pytest.mark.anyio
async def test_counts_requests_that_raise(fake_gc: Mock):
    middleware = GarbageCollectorMiddleware(failing_app, every_requests=1, min_interval_seconds=0)

    with pytest.raises(RuntimeError):
        await middleware(http_scope(), noop_receive, noop_send)
    await asyncio.sleep(0)

    fake_gc.collect.assert_called_once_with(2)


@pytest.mark.anyio
async def test_ignores_non_http_scopes(fake_gc: Mock):
    middleware = GarbageCollectorMiddleware(ok_app, every_requests=1, min_interval_seconds=0)

    await middleware({"type": "websocket"}, noop_receive, noop_send)
    await asyncio.sleep(0)

    assert middleware.requests == 0
    fake_gc.collect.assert_not_called()


@pytest.mark.anyio
async def test_lifespan_messages_are_forwarded(fake_gc: Mock):
    sent: List[Dict[str, Any]] = []

    async def lifespan_app(_scope: Dict[str, Any], _receive: Any, send: Any) -> None:
        await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})

    async def record(message: Dict[str, Any]) -> None:
        sent.append(message)

    middleware = GarbageCollectorMiddleware(lifespan_app)
    await middleware({"type": "lifespan"}, noop_receive, record)

    assert [message["type"] for message in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    fake_gc.freeze.assert_called_once_with()


@pytest.mark.usefixtures("fake_gc")
def test_respects_min_interval_with_jitter(monkeypatch: MonkeyPatch):
    monkeypatch.setattr(garbage_collector.random, "uniform", lambda low, high: high)
    middleware = GarbageCollectorMiddleware(ok_app, every_requests=1, min_interval_seconds=100)
    middleware.requests = 10

    assert middleware.jitter_seconds == 20
    middleware.last_collection = garbage_collector.time.monotonic() - 110
    assert middleware.should_collect() is False

    middleware.last_collection = garbage_collector.time.monotonic() - 121
    assert middleware.should_collect() is True


@pytest.mark.usefixtures("fake_gc")
def test_does_not_schedule_twice():
    middleware = GarbageCollectorMiddleware(ok_app, every_requests=1, min_interval_seconds=0)
    middleware.requests = 10
    middleware.scheduled = True

    assert middleware.should_collect() is False


@pytest.mark.usefixtures("fake_gc")
def test_does_not_collect_when_rss_is_below_limit_or_unknown(monkeypatch: MonkeyPatch):
    middleware = GarbageCollectorMiddleware(ok_app, max_rss_mb=100, min_interval_seconds=0)

    monkeypatch.setattr(garbage_collector, "read_rss_bytes", lambda: 50 * MEGABYTE)
    assert middleware.should_collect() is False

    monkeypatch.setattr(garbage_collector, "read_rss_bytes", lambda: None)
    assert middleware.should_collect() is False


@pytest.mark.usefixtures("fake_gc")
def test_does_not_check_rss_without_limit(monkeypatch: MonkeyPatch):
    reader = Mock(return_value=10_000 * MEGABYTE)
    monkeypatch.setattr(garbage_collector, "read_rss_bytes", reader)
    middleware = GarbageCollectorMiddleware(ok_app, min_interval_seconds=0)

    assert middleware.should_collect() is False
    reader.assert_not_called()


def test_collect_without_malloc_trim(fake_gc: Mock):
    middleware = GarbageCollectorMiddleware(ok_app, generation=0, trim_memory=False)
    middleware.requests = 5
    middleware.scheduled = True

    middleware.collect()

    assert middleware.malloc_trim is None
    fake_gc.collect.assert_called_once_with(0)
    assert middleware.requests == 0
    assert middleware.scheduled is False


def test_collect_resets_state_when_collection_fails(fake_gc: Mock):
    fake_gc.collect.side_effect = RuntimeError("gc failed")
    middleware = GarbageCollectorMiddleware(ok_app)
    middleware.scheduled = True

    with pytest.raises(RuntimeError):
        middleware.collect()

    assert middleware.scheduled is False


@pytest.mark.parametrize(
    "options, message",
    [
        ({"every_requests": 0}, "every_requests must be greater than zero"),
        ({"generation": 3}, "generation must be 0, 1 or 2"),
    ],
)
def test_rejects_invalid_options(options: Dict[str, Any], message: str):
    with pytest.raises(ValueError, match=message):
        GarbageCollectorMiddleware(ok_app, **options)


def test_to_megabytes():
    assert to_megabytes(3 * MEGABYTE) == 3
    assert to_megabytes(None) == 0


def test_load_malloc_trim_returns_libc_function(monkeypatch: MonkeyPatch):
    library = Mock()
    monkeypatch.setattr(garbage_collector.ctypes.util, "find_library", lambda name: "libc.so.6")
    monkeypatch.setattr(garbage_collector.ctypes, "CDLL", lambda name: library)

    assert load_malloc_trim() is library.malloc_trim


def test_load_malloc_trim_without_libc(monkeypatch: MonkeyPatch):
    monkeypatch.setattr(garbage_collector.ctypes.util, "find_library", lambda name: None)

    assert load_malloc_trim() is None


@pytest.mark.parametrize("error", [OSError("missing"), AttributeError("malloc_trim")])
def test_load_malloc_trim_when_unavailable(monkeypatch: MonkeyPatch, error: Exception):
    def broken_cdll(_name: str) -> None:
        raise error

    monkeypatch.setattr(garbage_collector.ctypes.util, "find_library", lambda name: "libc")
    monkeypatch.setattr(garbage_collector.ctypes, "CDLL", broken_cdll)

    assert load_malloc_trim() is None


def test_read_rss_bytes_from_statm(monkeypatch: MonkeyPatch, tmp_path):
    statm = tmp_path / "statm"
    statm.write_text("1000 250 10 1 0 100 0\n")
    monkeypatch.setattr(garbage_collector, "STATM_PATH", str(statm))

    assert read_rss_bytes() == 250 * os.sysconf("SC_PAGE_SIZE")


@pytest.mark.parametrize("content", [None, "", "abc def"])
def test_read_rss_bytes_when_unavailable(monkeypatch: MonkeyPatch, tmp_path, content):
    statm = tmp_path / "statm"
    if content is not None:
        statm.write_text(content)
    monkeypatch.setattr(garbage_collector, "STATM_PATH", str(statm))

    assert read_rss_bytes() is None
