"""Embedding compatibility checks and resumable, batched re-embedding."""
from __future__ import annotations

import asyncio

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.providers import LLMError
from app.models import ChannelConfig, Chunk, QAPair, SemanticCache
from app.rag.documents import bump_context_version
from app.rag.embeddings import embed_texts, embedding_signature
from app.services.settings_service import get_setting, set_setting

BATCH = 64


def _stale(model, signature: str):
    return or_(model.embedding_version.is_(None), model.embedding_version != signature)


async def embedding_status(session: AsyncSession) -> dict:
    signature = await embedding_signature(session)
    counts = {}
    for name, model in (("chunks", Chunk), ("qa", QAPair), ("cache", SemanticCache)):
        counts[name] = int((await session.execute(
            select(func.count()).select_from(model).where(_stale(model, signature)))).scalar_one())
    real_channel = bool((await session.execute(select(ChannelConfig.id).where(
        ChannelConfig.enabled.is_(True)).limit(1))).first())
    provider = await get_setting(session, "embedding_provider") or "hash"
    return {"provider": provider, "model": await get_setting(session, "embedding_model") or "",
            "dimension": int(signature.rsplit(":", 1)[-1]), "version": signature,
            "mismatch": counts, "mismatch_total": sum(counts.values()),
            "hash_warning": provider == "hash" and real_channel,
            "progress": await get_setting(session, "embedding_reembed_progress") or {"status": "idle", "done": 0, "total": 0}}


QUOTA_WAIT_S = 65        # free-tier embedding quotas are per minute
QUOTA_MAX_WAITS = 30


async def _embed_waiting_for_quota(session: AsyncSession, texts: list[str], *, done: int, total: int) -> list:
    """A 429 (quota) is not a failure: wait for the next minute and go on, up to QUOTA_MAX_WAITS times."""
    for attempt in range(QUOTA_MAX_WAITS + 1):
        try:
            return await embed_texts(session, texts)
        except LLMError as exc:
            if getattr(exc, "status", None) != 429 or attempt == QUOTA_MAX_WAITS:
                raise
            await set_setting(session, "embedding_reembed_progress",
                              {"status": "running", "done": done, "total": total, "note": "waiting for the provider's quota"})
            await session.commit()
            await asyncio.sleep(QUOTA_WAIT_S)
    raise RuntimeError("unreachable")


async def reembed_all(maker: async_sessionmaker[AsyncSession]) -> dict:
    """Resume by repeatedly selecting only rows whose signature is stale; commit every batch."""
    async with maker() as session:
        state = await embedding_status(session)
        signature, total = state["version"], state["mismatch_total"]
        await set_setting(session, "embedding_reembed_progress", {"status": "running", "done": 0, "total": total})
        await session.commit()
    done = 0
    try:
        for model, text_col in ((Chunk, Chunk.content), (QAPair, QAPair.question), (SemanticCache, SemanticCache.question)):
            while True:
                async with maker() as session:
                    # Re-read the active signature each batch. A mid-job config change leaves remaining rows stale
                    # for the next run instead of falsely labelling vectors.
                    current = await embedding_signature(session)
                    if current != signature:
                        raise RuntimeError("embedding configuration changed during re-embedding; run again")
                    rows = (await session.execute(select(model).where(_stale(model, signature))
                                                  .order_by(model.id).limit(BATCH))).scalars().all()
                    if not rows:
                        break
                    vectors = await _embed_waiting_for_quota(session, [getattr(row, text_col.key) for row in rows],
                                                             done=done, total=total)
                    for row, vector in zip(rows, vectors):
                        row.embedding, row.embedding_version = vector, signature
                    done += len(rows)
                    await set_setting(session, "embedding_reembed_progress", {"status": "running", "done": done, "total": total})
                    await session.commit()
        async with maker() as session:
            await bump_context_version(session)
            progress = {"status": "done", "done": done, "total": total}
            await set_setting(session, "embedding_reembed_progress", progress)
            await session.commit()
            return progress
    except Exception as exc:
        async with maker() as session:
            await set_setting(session, "embedding_reembed_progress",
                              {"status": "failed", "done": done, "total": total, "error": str(exc)[:300]})
            await session.commit()
        raise
