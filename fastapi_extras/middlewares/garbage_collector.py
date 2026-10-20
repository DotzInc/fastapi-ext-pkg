import asyncio
import ctypes
import ctypes.util
import gc
import logging
import os
import random
import time
from typing import Awaitable, Callable, Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

STATM_PATH = "/proc/self/statm"
MEGABYTE = 1024 * 1024
JITTER_RATIO = 0.2


def load_malloc_trim() -> Optional[Callable[[int], int]]:
    library = ctypes.util.find_library("c")
    if not library:
        return None

    try:
        return ctypes.CDLL(library).malloc_trim
    except (OSError, AttributeError):
        return None


def read_rss_bytes() -> Optional[int]:
    try:
        with open(STATM_PATH) as statm:
            resident_pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None

    return resident_pages * os.sysconf("SC_PAGE_SIZE")


def to_megabytes(value: Optional[int]) -> float:
    return (value or 0) / MEGABYTE


class GarbageCollectorMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        every_requests: int = 5000,
        max_rss_mb: Optional[int] = None,
        min_interval_seconds: float = 60.0,
        generation: int = 2,
        freeze_on_startup: bool = True,
        trim_memory: bool = True,
    ) -> None:
        if every_requests < 1:
            raise ValueError("every_requests must be greater than zero")

        if generation not in (0, 1, 2):
            raise ValueError("generation must be 0, 1 or 2")

        self.app = app
        self.every_requests = every_requests
        self.max_rss_bytes = max_rss_mb * MEGABYTE if max_rss_mb else None
        self.min_interval_seconds = min_interval_seconds
        self.jitter_seconds = random.uniform(0, min_interval_seconds * JITTER_RATIO)
        self.generation = generation
        self.freeze_on_startup = freeze_on_startup
        self.malloc_trim = load_malloc_trim() if trim_memory else None
        self.requests = 0
        self.last_collection = time.monotonic()
        self.scheduled = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, self.wrap_lifespan_send(send))

        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        try:
            await self.app(scope, receive, send)
        finally:
            self.requests += 1
            if self.should_collect():
                self.scheduled = True
                asyncio.get_running_loop().call_soon(self.collect)

    def wrap_lifespan_send(self, send: Send) -> Callable[[Message], Awaitable[None]]:
        async def lifespan_send(message: Message) -> None:
            if message["type"] == "lifespan.startup.complete" and self.freeze_on_startup:
                gc.collect()
                gc.freeze()
                logger.info("Garbage collector froze %s startup objects", gc.get_freeze_count())

            await send(message)

        return lifespan_send

    def should_collect(self) -> bool:
        if self.scheduled:
            return False

        elapsed = time.monotonic() - self.last_collection
        if elapsed < self.min_interval_seconds + self.jitter_seconds:
            return False

        if self.requests >= self.every_requests:
            return True

        if self.max_rss_bytes is None:
            return False

        rss = read_rss_bytes()
        return rss is not None and rss >= self.max_rss_bytes

    def collect(self) -> None:
        started_at = time.perf_counter()
        rss_before = read_rss_bytes()

        try:
            collected = gc.collect(self.generation)
            if self.malloc_trim is not None:
                self.malloc_trim(0)
        finally:
            self.requests = 0
            self.last_collection = time.monotonic()
            self.scheduled = False

        logger.info(
            "Garbage collection generation=%s collected=%s duration_ms=%.2f "
            "rss_before_mb=%.2f rss_after_mb=%.2f",
            self.generation,
            collected,
            (time.perf_counter() - started_at) * 1000,
            to_megabytes(rss_before),
            to_megabytes(read_rss_bytes()),
        )
