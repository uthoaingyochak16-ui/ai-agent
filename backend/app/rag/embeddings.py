"""Embeddings behind one interface. Configurable provider; the SAME provider/model must be used for documents and questions.

Text sent to an external embedding API goes through the same redaction as chat prompts (unless the provider is allow_pii).
`hash` is an offline, deterministic character-n-gram embedder: fine for development and tests, weak semantically.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.retry import retry_async
from app.llm.providers import LLMError
from app.models import LLMProviderConfig
from app.privacy.guard import PrivacyGuard, ProviderPolicy
from app.services.settings_service import get_setting


class Embedder(ABC):
    name: str
    external = True

    def __init__(self, dim: int):
        self.dim = dim

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder(Embedder):
    name, external = "hash", False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        t = re.sub(r"[^\w\s]", " ", text.lower())
        t = re.sub(r"\s+", " ", t).strip()
        feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in t.split()]
        padded = f"  {t}  "
        feats += [(f"c:{padded[i:i + 3]}", 0.5) for i in range(len(padded) - 2)]
        v = [0.0] * self.dim
        for f, w in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += w if (h >> 63) & 1 else -w
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class _HttpEmbedder(Embedder):
    def __init__(self, dim: int, api_key: str, model: str, base_url: str | None = None, timeout_s: float = 30.0):
        super().__init__(dim)
        self.api_key, self.model, self.base = api_key, model, (base_url or self.default_base).rstrip("/")
        self.timeout_s = timeout_s

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        async def once():
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                    r = await c.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise LLMError(f"{self.name} embeddings network error", retryable=True) from exc
            if r.status_code >= 400:
                raise LLMError(f"{self.name} embeddings {r.status_code}: {r.text[:160]}", status=r.status_code,
                               retryable=r.status_code in (408, 429, 500, 502, 503, 504))
            return r.json()
        return await retry_async(once, retries=2, is_retryable=lambda e: isinstance(e, LLMError) and e.retryable)


class OpenAIEmbedder(_HttpEmbedder):
    name, default_base = "openai", "https://api.openai.com"

    async def embed(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            d = await self._post(f"{self.base}/v1/embeddings", {"authorization": f"Bearer {self.api_key}"},
                                 {"model": self.model, "input": texts[i:i + 64], "dimensions": self.dim})
            out += [x["embedding"] for x in sorted(d["data"], key=lambda x: x["index"])]
        return out


class GoogleEmbedder(_HttpEmbedder):
    name, default_base = "google", "https://generativelanguage.googleapis.com"

    async def embed(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            reqs = [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]},
                     "outputDimensionality": self.dim} for t in texts[i:i + 64]]
            d = await self._post(f"{self.base}/v1beta/models/{self.model}:batchEmbedContents",
                                 {"x-goog-api-key": self.api_key}, {"requests": reqs})
            out += [e["values"] for e in d["embeddings"]]
        return out


async def get_embedder(session: AsyncSession) -> tuple[Embedder, ProviderPolicy]:
    dim = get_settings().embedding_dim
    name = await get_setting(session, "embedding_provider") or "hash"
    if name == "hash":
        return HashingEmbedder(dim), ProviderPolicy(name="hash", allow_pii=True)
    cfg = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == name))).scalar_one_or_none()
    if cfg is None or not cfg.enabled or not cfg.api_key_enc:
        raise LLMError(f"embedding provider '{name}' is not configured")
    model = await get_setting(session, "embedding_model") or ""
    key = crypto.decrypt(cfg.api_key_enc, aad=f"llm:{name}")
    cls = {"openai": OpenAIEmbedder, "google": GoogleEmbedder}[name]
    return cls(dim, key, model, cfg.base_url, cfg.timeout_s), ProviderPolicy(name=name, allow_pii=cfg.allow_pii)


async def embed_texts(session: AsyncSession, texts: list[str], *, embedder=None) -> list[list[float]]:
    """Embed (after redaction for external providers). Returns one vector per text."""
    if not texts:
        return []
    emb, policy = embedder or await get_embedder(session)
    if emb.external:
        guard = PrivacyGuard(policy)
        texts = [guard.new_redactor().redact(t) for t in texts]  # fresh map per text: embeddings are never restored
    return await emb.embed(texts)
