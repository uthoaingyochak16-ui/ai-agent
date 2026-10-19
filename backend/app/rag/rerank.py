"""Optional cross-encoder reranking for knowledge retrieval.

Only text already selected by the cheap hybrid pre-filter is sent. External requests are redacted with the
same provider policy used by embeddings and answer generation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.llm.providers import LLMError
from app.models import LLMProviderConfig
from app.privacy.guard import PrivacyGuard, ProviderPolicy
from app.services.settings_service import get_setting

DEFAULT_MIN_SCORE = {"cohere": 0.2, "local": 0.0}


class Reranker(ABC):
    name: str

    def __init__(self, api_key: str, model: str, base_url: str, timeout_s: float = 30.0):
        self.api_key, self.model, self.base_url, self.timeout_s = api_key, model, base_url.rstrip("/"), timeout_s

    @abstractmethod
    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Return (input document index, relevance score), best first."""

    async def _request(self, url: str, body: dict, headers: dict | None = None) -> object:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMError(f"{self.name} rerank network error", retryable=True) from exc
        if response.status_code >= 400:
            raise LLMError(f"{self.name} rerank {response.status_code}: {response.text[:160]}",
                           status=response.status_code, retryable=response.status_code in (408, 429, 500, 502, 503, 504))
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(f"{self.name} rerank returned invalid JSON") from exc


def _scores(data: object) -> list[tuple[int, float]]:
    rows = data.get("results", data.get("data", [])) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise LLMError("rerank response has no result list")
    out = []
    for row in rows:
        if not isinstance(row, dict) or "index" not in row:
            continue
        score = row.get("relevance_score", row.get("score"))
        if score is not None:
            out.append((int(row["index"]), float(score)))
    if not out and rows:
        raise LLMError("rerank response has no scores")
    return sorted(out, key=lambda item: -item[1])


class CohereReranker(Reranker):
    name = "cohere"

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        data = await self._request(f"{self.base_url}/v2/rerank",
                                   {"model": self.model, "query": query, "documents": documents,
                                    "top_n": len(documents), "return_documents": False},
                                   {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"})
        return _scores(data)


class LocalReranker(Reranker):
    name = "local"

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        url = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        # TEI uses `texts`; OpenAI-style rerank servers often use `documents`.
        try:
            data = await self._request(url, {"model": self.model, "query": query, "texts": documents,
                                             "top_n": len(documents), "return_text": False}, headers)
        except LLMError as exc:
            if exc.status not in (400, 422):
                raise
            data = await self._request(url, {"model": self.model, "query": query, "documents": documents,
                                             "top_n": len(documents), "return_documents": False}, headers)
        return _scores(data)


async def get_reranker(session: AsyncSession) -> tuple[Reranker | None, ProviderPolicy | None, float]:
    name = await get_setting(session, "rerank_provider") or "none"
    if name == "none":
        return None, None, 0.0
    if name not in ("cohere", "local"):
        raise LLMError(f"unknown rerank provider '{name}'")
    config_name = f"rerank_{name}"
    cfg = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == config_name))).scalar_one_or_none()
    if cfg is None or not cfg.enabled or not cfg.base_url or (name == "cohere" and not cfg.api_key_enc):
        raise LLMError(f"rerank provider '{name}' is not configured")
    key = crypto.decrypt(cfg.api_key_enc, aad=f"rerank:{name}") if cfg.api_key_enc else ""
    model = await get_setting(session, "rerank_model") or "rerank-multilingual-v3.0"
    cls = CohereReranker if name == "cohere" else LocalReranker
    configured_min = await get_setting(session, "rerank_min_score")
    minimum = DEFAULT_MIN_SCORE[name] if configured_min is None else float(configured_min)
    policy = ProviderPolicy(name=f"rerank_{name}", allow_pii=cfg.allow_pii,
                            custom_patterns=tuple(await get_setting(session, "privacy_custom_patterns") or ()),
                            never_send=frozenset(await get_setting(session, "privacy_never_send") or ()),
                            name_mode=await get_setting(session, "name_mode") or "placeholder")
    return cls(key, model, cfg.base_url, cfg.timeout_s), policy, minimum


async def rerank_texts(session: AsyncSession, query: str, documents: list[str], *, reranker=None):
    engine, policy, minimum = reranker or await get_reranker(session)
    if engine is None:
        return None, minimum
    guard = PrivacyGuard(policy)
    safe_query = guard.new_redactor().redact(query)
    safe_docs = [guard.new_redactor().redact(text) for text in documents]
    return await engine.rerank(safe_query, safe_docs), minimum
