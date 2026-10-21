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
from app.services.embedding_maintenance import embed_pending, reembed_all
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


async def process_comment_job(ctx, comment_row_id: int) -> str:
    """A comment on one of the Page's posts (services/comments.py)."""
    from app.services.comments import process_comment
    return await process_comment(ctx["maker"], comment_row_id)


async def publish_posts_job(ctx) -> int:
    """Every minute: scheduled posts whose time has come are published (services/posts.py)."""
    from app.services.posts import publish_due
    return await publish_due(ctx["maker"])


async def sync_posts_job(ctx) -> dict:
    """Every 3 hours: the connected accounts' latest posts and their numbers, for the analysis."""
    from app.services.posts import accounts, sync
    from app.services.reply_engine import get_llm_service
    async with ctx["maker"]() as session:
        if not await accounts(session):
            return {"status": "no accounts"}
        res = await sync(session, get_llm_service(session))
        await session.commit()
        return res


async def broadcast_job(ctx, broadcast_id: int) -> dict:
    """A WhatsApp broadcast's messages (services/broadcast.py)."""
    from app.services.broadcast import run
    return await run(ctx["maker"], broadcast_id)


async def daily_social_job(ctx) -> dict:
    """02:00 Dhaka: today's audience numbers of every account, and invitation / reminder drafts for new programs."""
    from app.services import posts, social
    from app.services.reply_engine import get_llm_service
    async with ctx["maker"]() as session:
        if not await posts.accounts(session):
            return {"status": "no accounts"}
        stats = await social.snapshot(session)
        drafts = await posts.program_drafts(session, get_llm_service(session))
        await session.commit()
        return {"accounts": len(stats), "program_drafts": drafts}


async def weekly_social_job(ctx) -> dict:
    """Saturdays 09:00 Dhaka: what guests asked, the weekly report (alert webhook), and the AI's plan as drafts if on."""
    from app.services import posts, social
    from app.services.reply_engine import get_llm_service
    from app.services.settings_service import get_setting
    out: dict = {}
    async with ctx["maker"]() as session:
        llm = get_llm_service(session)
        await social.question_trends(session, llm)
        if await get_setting(session, "weekly_report_enabled"):
            out["report"] = (await social.weekly_report(session, llm)).id
        if await get_setting(session, "auto_plan_weekly") and await posts.accounts(session):
            out["plan"] = await posts.auto_plan(session, llm, language=await get_setting(session, "report_language") or "bn")
        await session.commit()
    return out


async def ack_event_job(ctx, event_id: int) -> None:
    await acknowledge_event(ctx["maker"], event_id)


async def process_document_job(ctx, doc_id: int) -> str:
    async with ctx["maker"]() as session:
        return await process_document(session, doc_id)


async def reembed_all_job(ctx) -> dict:
    return await reembed_all(ctx["maker"])


async def embed_pending_job(ctx) -> int:
    """Every 20 min: document pieces saved while the embedding provider was down get their vectors."""
    return await embed_pending(ctx["maker"])


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
    n += await _requeue_stale_comments(ctx, now)
    if n:
        logger.info("requeued %s stale events", n)
    return n


async def _requeue_stale_comments(ctx, now: datetime) -> int:
    """Comments whose job was lost, like the events above (their reply delay is on top of the usual wait)."""
    from app.models import PostComment
    from app.services.comments import enqueue
    from app.services.settings_service import get_setting
    async with ctx["maker"]() as session:
        delay = float(await get_setting(session, "comment_reply_delay_seconds") or 0)
        await session.execute(update(PostComment).where(
            PostComment.status == "processing", PostComment.received_at < now - timedelta(minutes=5, seconds=delay)
        ).values(status="queued"))
        rows = (await session.execute(select(PostComment.id).where(
            PostComment.status == "queued", PostComment.received_at < now - timedelta(seconds=60 + delay)).limit(100))).scalars().all()
        await session.commit()
    for row_id in rows:
        await enqueue(ctx["redis"], row_id, 0, tag=f"-r{int(now.timestamp()) // 60}")
    return len(rows)


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


async def main_app_programs_job(ctx) -> dict:
    """Every 15 min: the main application's open programs and their registration links (services/main_app.py)."""
    from app.services import main_app
    if not main_app.configured():
        return {"status": "not configured"}
    async with ctx["maker"]() as session:
        return await main_app.sync_programs(session)


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
                 func(eval_run_job, timeout=3600), func(keygen_job, timeout=3600),
                 func(process_comment_job, timeout=180), func(broadcast_job, timeout=3600)]
    cron_jobs = [cron(requeue_stale, second=0), cron(purge_retention, hour=21, minute=30),  # 03:30 Dhaka
                 cron(alert_job, minute=set(range(0, 60, 5)), second=30),                 # every 5 min
                 cron(embed_pending_job, minute={7, 27, 47}, timeout=600),                # every 20 min
                 cron(programs_job, hour=18, minute=5),                                   # 00:05 Dhaka
                 cron(program_sync_job, hour=18, minute=15, timeout=60),                  # 00:15 Dhaka
                 cron(keyword_index_job, hour=21, minute=45),                             # 03:45 Dhaka
                 cron(learning_job, hour=22, minute=0, timeout=900),                      # 04:00 Dhaka
                 cron(main_app_programs_job, minute=set(range(2, 60, 15)), timeout=60, run_at_startup=True),
                 cron(publish_posts_job, second=20, timeout=120),                         # every minute
                 cron(sync_posts_job, hour=set(range(0, 24, 3)), minute=20, timeout=600),
                 cron(daily_social_job, hour=20, minute=0, timeout=900),                  # 02:00 Dhaka
                 cron(weekly_social_job, weekday=5, hour=3, minute=0, timeout=900)]       # Saturday 09:00 Dhaka
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    keep_result = 0
    max_jobs = get_settings().worker_max_jobs
    job_timeout = 120
