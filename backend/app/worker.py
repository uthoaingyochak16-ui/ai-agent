"""Arq worker: processes webhook events and re-queues stuck ones."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import get_sessionmaker
from app.models import WebhookEvent
from app.rag.documents import process_document
from app.services.ingest import process_event
from app.services.retention import purge_old_conversations

logger = logging.getLogger(__name__)


async def startup(ctx) -> None:
    setup_logging(get_settings().log_level)
    ctx["maker"] = get_sessionmaker()


async def process_event_job(ctx, event_id: int) -> str:
    return await process_event(ctx["maker"], event_id)


async def process_document_job(ctx, doc_id: int) -> str:
    async with ctx["maker"]() as session:
        return await process_document(session, doc_id)


async def requeue_stale(ctx) -> int:
    """Events whose enqueue was lost (Redis outage, crash) are still 'queued' in Postgres: push them again."""
    now = datetime.now(timezone.utc)
    async with ctx["maker"]() as session:
        # a worker that died mid-processing leaves 'processing' behind
        await session.execute(update(WebhookEvent).where(
            WebhookEvent.status == "processing", WebhookEvent.received_at < now - timedelta(minutes=5)
        ).values(status="queued"))
        rows = (await session.execute(select(WebhookEvent.id, WebhookEvent.attempts).where(
            WebhookEvent.status == "queued", WebhookEvent.received_at < now - timedelta(seconds=45)).limit(200))).all()
        await session.commit()
    n = 0
    for event_id, attempts in rows:
        job = await ctx["redis"].enqueue_job("process_event_job", event_id, _job_id=f"evt-{event_id}-r{attempts}-{int(now.timestamp()) // 60}")
        n += 1 if job else 0
    if n:
        logger.info("requeued %s stale events", n)
    return n


async def purge_retention(ctx) -> dict:
    async with ctx["maker"]() as session:
        return await purge_old_conversations(session)


class WorkerSettings:
    functions = [process_event_job, func(process_document_job, timeout=900)]  # OCR of a long scan takes minutes
    cron_jobs = [cron(requeue_stale, second=0), cron(purge_retention, hour=21, minute=30)]  # 03:30 Dhaka
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    keep_result = 0
    max_jobs = 20
    job_timeout = 120
