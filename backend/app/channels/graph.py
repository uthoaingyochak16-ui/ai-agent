"""Thin Graph API client: bearer auth, timeouts, retries with backoff on transient errors."""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.core.retry import retry_async

logger = logging.getLogger(__name__)

# Meta error codes that are worth retrying (rate limits / temporary). Verify against Meta's error reference.
_RETRYABLE_CODES = {1, 2, 4, 17, 32, 613, 80007, 130429, 131056}


class GraphError(Exception):
    def __init__(self, status: int, code: int | None, subcode: int | None, message: str):
        super().__init__(f"Graph API error {status} code={code} subcode={subcode}: {message}")
        self.status, self.code, self.subcode, self.message = status, code, subcode, message

    @property
    def retryable(self) -> bool:
        return self.status in (429, 500, 502, 503, 504) or (self.code in _RETRYABLE_CODES)


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, GraphError):
        return exc.retryable
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


async def graph_request(method: str, path: str, *, token: str, base_url: str, version: str,
                        json: dict | None = None, params: dict | None = None) -> dict:
    s = get_settings()
    url = f"{base_url.rstrip('/')}/{version}/{path.lstrip('/')}"

    async def once() -> dict:
        async with httpx.AsyncClient(timeout=s.http_timeout_s) as client:
            resp = await client.request(method, url, json=json, params=params,
                                        headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            raise GraphError(resp.status_code, err.get("code"), err.get("error_subcode"),
                             err.get("message") or resp.text[:200])
        try:
            return resp.json()
        except ValueError:  # 200 with an empty / non-JSON body
            return {}

    return await retry_async(once, retries=s.http_max_retries, is_retryable=_retryable)
