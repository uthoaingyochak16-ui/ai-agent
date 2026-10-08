from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int,
    is_retryable: Callable[[Exception], bool],
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> T:
    """Exponential backoff with full jitter. `retries` = extra attempts after the first."""
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            if attempt >= retries or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2**attempt))
            await asyncio.sleep(random.uniform(0, delay))
            attempt += 1
