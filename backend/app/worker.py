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
from app.services.ingest import acknowledge_event, process_event
from app.rag.programs import purge_expired
from app.services.monitoring import check_alerts, send_alerts
from app.services.retention import purge_old_conversations
from app.services.embedding_maintenance import reembed_all
from app.services.evaluation import run_eval

logger = logging.getLogger(__name__)


async def startup(ctx) -> None:
    setup_logging(get_settings().log_level)
    ctx["maker"] = get_sessionmaker()
    try:  # first start / restored data: make sure the keyword index exists
        from app.rag.keyword_index import ensure_index
        async with ctx["maker"]() as session:
            await ensure_index(session)
    except Exception:  # noqa: BLE001 - the index is rebuilt nightly; a failure here must not stop the worker
        logger.exception("keyword index start-up check failed")


async def process_event_job(ctx, event_id: int) -> str:
    return await process_event(ctx["maker"], event_id, queue=ctx["redis"])


async def ack_event_job(ctx, event_id: int) -> None:
    await acknowledge_event(ctx["maker"], event_id)


async def process_document_job(ctx, doc_id: int) -> str:
    async with ctx["maker"]() as session:
        return await process_document(session, doc_id)


async def reembed_all_job(ctx) -> dict:
    return await reembed_all(ctx["maker"])


async def eval_run_job(ctx, run_id: int) -> dict:
    return await run_eval(ctx["maker"], run_id)


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


async def programs_job(ctx) -> int:
    """00:05 Bangladesh time: programs whose last day was yesterday or earlier are deleted."""
    async with ctx["maker"]() as session:
        n = await purge_expired(session)
        if n:
            from app.rag.documents import bump_context_version
            await bump_context_version(session)
        await session.commit()
        return n


async def program_sync_job(ctx) -> dict:
    """00:15 Bangladesh time: import official public Event metadata without deleting manual rows."""
    from app.services.program_sync import sync_programs
    from app.services.settings_service import get_setting
    async with ctx["maker"]() as session:
        if not await get_setting(session, "program_sync_enabled"):
            return {"status": "disabled"}
        result = await sync_programs(session)
        await session.commit()
        return result


async def keygen_job(ctx, types: list[str], limit: int) -> dict:
    """AI keywords for records that have none (Keywords tab -> Generate with AI). Progress is kept in a setting."""
    from app.llm.service import LLMUnavailable
    from app.rag.keyword_tools import ai_generate, pending_ai_records
    from app.services.reply_engine import get_llm_service
    from app.services.settings_service import set_setting
    async with ctx["maker"]() as session:
        todo = await pending_ai_records(session, types, limit)
        progress = {"status": "running", "done": 0, "total": len(todo), "tokens": 0, "kept": 0, "rejected": 0}
        await set_setting(session, "keygen_progress", progress)
        await session.commit()
        llm = get_llm_service(session)
        for st, ref in todo:
            try:
                res = await ai_generate(session, llm, st, ref)
                progress["tokens"] += res.get("tokens", 0)
                progress["kept"] += len(res["kept"])
                progress["rejected"] += len(res["rejected"])
                await session.commit()
            except LLMUnavailable as exc:
                await session.rollback()
                progress.update(status="error", error=f"Set the “Keyword generation” route in AI Settings → Model routing ({exc})")
                break
            except Exception:  # noqa: BLE001 - one record failing must not stop the rest
                await session.rollback()
                logger.exception("keyword generation failed for %s %s", st, ref)
            progress["done"] += 1
            await set_setting(session, "keygen_progress", dict(progress))
            await session.commit()
        if progress["status"] == "running":
            progress["status"] = "done"
        await set_setting(session, "keygen_progress", progress)
        await session.commit()
        return progress


async def keyword_index_job(ctx) -> dict:
    """03:45 Dhaka: build the keyword index again from the data (drops rows of records that no longer exist)."""
    from app.rag.keyword_index import rebuild_index
    async with ctx["maker"]() as session:
        return await rebuild_index(session)


async def learning_job(ctx) -> int:
    """04:00 Dhaka: repeated unknown questions and wrong-answer feedback get a suggested Q&A draft (admin approves)."""
    from app.services.learning import prepare_drafts, refresh_suggestions
    from app.services.reply_engine import get_llm_service
    async with ctx["maker"]() as session:
        await refresh_suggestions(session)
        n = await prepare_drafts(session, get_llm_service(session))
        await session.commit()
        return n


async def alert_job(ctx) -> list[str]:
    """Every 5 min: stuck queue, failing sends, budget -> webhook (deduplicated per issue per hour)."""
    async with ctx["maker"]() as session:
        return await send_alerts(session, await check_alerts(session))


class WorkerSettings:
    functions = [func(process_event_job, timeout=240), func(ack_event_job, timeout=30),
                 func(process_document_job, timeout=900), func(reembed_all_job, timeout=3600),
                 func(eval_run_job, timeout=3600), func(keygen_job, timeout=3600)]
    cron_jobs = [cron(requeue_stale, second=0), cron(purge_retention, hour=21, minute=30),  # 03:30 Dhaka
                 cron(alert_job, minute=set(range(0, 60, 5)), second=30),                 # every 5 min
                 cron(programs_job, hour=18, minute=5),                                   # 00:05 Dhaka
                 cron(program_sync_job, hour=18, minute=15, timeout=60),                  # 00:15 Dhaka
                 cron(keyword_index_job, hour=21, minute=45),                             # 03:45 Dhaka
                 cron(learning_job, hour=22, minute=0, timeout=900)]                      # 04:00 Dhaka
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    keep_result = 0
    max_jobs = 20
    job_timeout = 120
