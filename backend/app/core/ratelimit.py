"""Fixed-window rate limiter backed by Redis. Fails open if Redis is down."""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def rate_limit(name: str, limit: int, window_s: int):
    async def dep(request: Request) -> None:
        ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
        key = f"rl:{name}:{ip}"
        try:
            r = get_redis()
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, window_s)
            if n > limit:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": str(window_s)})
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("rate limiter unavailable, failing open")

    return dep
