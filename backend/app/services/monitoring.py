"""Operational monitoring: system health numbers, AI quality metrics, and threshold alerts to a webhook.

Every number carries a state ("ok" | "amber" | "red") so the dashboard and the alert cron agree on what is wrong.
Alerts go to `alert_webhook_url` as {"text": ...} (Slack incoming webhooks and Telegram sendMessage URLs with
?chat_id=... both accept it), at most once per issue per hour.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import budget_state
from app.models import ChannelIdentity, Conversation, LLMCall, Message, WebhookEvent
from app.services import security
from app.services.embedding_maintenance import embedding_status
from app.services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)
ALERT_REPEAT = timedelta(hours=1)
REAL = ~ChannelIdentity.external_id.like("test:%")


def _state(value: float, amber: float, red: float) -> str:
    return "red" if value >= red else "amber" if value >= amber else "ok"


async def system_health(session: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    stuck_min = float(await get_setting(session, "alert_stuck_minutes") or 5)
    counts = dict((await session.execute(select(WebhookEvent.status, func.count()).where(
        WebhookEvent.status.in_(("queued", "processing", "failed"))).group_by(WebhookEvent.status))).all())
    oldest = (await session.execute(select(func.min(WebhookEvent.received_at)).where(
        WebhookEvent.status.in_(("queued", "processing"))))).scalar_one_or_none()
    oldest_s = int((now - oldest).total_seconds()) if oldest else 0
    day = now - timedelta(hours=24)
    sends = dict((await session.execute(select(Message.delivery_status, func.count()).where(
        Message.created_at >= day, Message.delivery_status.in_(("failed", "blocked"))).group_by(Message.delivery_status))).all())
    calls, errors = (await session.execute(select(func.count(), func.count().filter(LLMCall.status == "error")).where(
        LLMCall.created_at >= day, LLMCall.is_test.is_(False)))).one()
    p95 = (await session.execute(select(func.percentile_cont(0.95).within_group(LLMCall.latency_ms)).where(
        LLMCall.created_at >= day, LLMCall.status == "ok", LLMCall.is_test.is_(False)))).scalar_one_or_none()
    err_rate = (errors / calls) if calls else 0.0
    shared, asked = await _card_events(session, day)
    b = await budget_state(session)
    emb = await embedding_status(session)
    return {
        "events": {"queued": counts.get("queued", 0), "processing": counts.get("processing", 0), "failed": counts.get("failed", 0),
                   "oldest_pending_s": oldest_s, "state": "red" if oldest_s >= stuck_min * 60 else
                   "amber" if counts.get("failed", 0) or oldest_s >= 60 else "ok"},
        "delivery_24h": {"failed": sends.get("failed", 0), "blocked": sends.get("blocked", 0),
                         "state": _state(sends.get("failed", 0), 1, 10)},
        "llm_24h": {"calls": calls, "errors": errors, "error_rate": round(err_rate, 4),
                    "p95_latency_ms": int(p95) if p95 is not None else None,
                    "state": "red" if calls >= 5 and err_rate >= 0.2 else "amber" if err_rate >= 0.05 or (p95 or 0) >= 15000 else "ok"},
        "budget": {"level": b.level, "percent": round(b.percent, 1),
                   "state": {"exceeded": "red", "alert": "amber"}.get(b.level, "ok")},
        "embeddings": {"provider": emb["provider"], "hash_warning": emb["hash_warning"], "mismatch_total": emb["mismatch_total"],
                       "state": "red" if emb["hash_warning"] else "amber" if emb["mismatch_total"] else "ok"},
        "security_24h": {"card_shared": shared, "card_request_blocked": asked, "state": "red" if shared or asked else "ok"},
    }


async def _card_events(session: AsyncSession, since: datetime) -> tuple[int, int]:
    """(card numbers guests sent, replies asking for card details that were replaced) since `since`."""
    rows = dict((await session.execute(select(Message.flag, func.count()).where(
        Message.created_at >= since, Message.flag.in_(security.FLAGS)).group_by(Message.flag))).all())
    return rows.get(security.CARD_SHARED, 0), rows.get(security.CARD_REQUEST_BLOCKED, 0)


async def ai_metrics(session: AsyncSession, days: int = 14) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    real_conv = select(Conversation.id).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id).where(REAL)
    active = select(Message.conversation_id).where(Message.created_at >= since, Message.role == "user",
                                                   Message.conversation_id.in_(real_conv)).distinct().subquery()
    n_conv = (await session.execute(select(func.count()).select_from(active))).scalar_one()
    with_ai = select(Message.conversation_id).where(Message.role == "assistant", Message.created_at >= since)
    with_staff = select(Message.conversation_id).where(Message.role == "agent", Message.created_at >= since)
    handed = (await session.execute(select(func.count()).select_from(Conversation).where(
        Conversation.id.in_(select(active.c.conversation_id)),
        (Conversation.status == "handover") | Conversation.id.in_(with_staff)))).scalar_one()
    resolved = (await session.execute(select(func.count()).select_from(Conversation).where(
        Conversation.id.in_(select(active.c.conversation_id)), Conversation.id.in_(with_ai),
        Conversation.status != "handover", ~Conversation.id.in_(with_staff)))).scalar_one()
    path = Message.sources_json["path"].astext
    bot, no_ctx = (await session.execute(select(func.count(), func.count().filter(path.in_(("no_context", "llm_unavailable"))))
                                         .where(Message.role == "assistant", Message.created_at >= since,
                                                Message.conversation_id.in_(real_conv)))).one()
    cost, cost_convs = (await session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0), func.count(func.distinct(LLMCall.conversation_id)))
                                              .where(LLMCall.created_at >= since, LLMCall.is_test.is_(False),
                                                     LLMCall.conversation_id.is_not(None)))).one()
    median_s = (await session.execute(text("""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM a.created_at - u.created_at))
        FROM messages u
        JOIN conversations c ON c.id = u.conversation_id
        JOIN channel_identities i ON i.id = c.channel_identity_id AND i.external_id NOT LIKE 'test:%'
        JOIN LATERAL (SELECT m.created_at FROM messages m WHERE m.conversation_id = u.conversation_id AND m.id > u.id
                      AND m.role = 'assistant' ORDER BY m.id LIMIT 1) a ON true
        WHERE u.role = 'user' AND u.created_at >= :since"""), {"since": since})).scalar_one_or_none()
    rate = lambda n, d: round(n / d, 4) if d else None  # noqa: E731
    return {"days": days, "conversations": n_conv, "resolution_rate": rate(resolved, n_conv), "handover_rate": rate(handed, n_conv),
            "no_context_rate": rate(no_ctx, bot), "avg_cost_per_conversation": round(float(cost) / cost_convs, 6) if cost_convs else None,
            "median_reply_latency_s": round(float(median_s), 2) if median_s is not None else None}


# ---- alerts ---------------------------------------------------------------------------------------------------
@dataclass
class Alert:
    key: str
    text: str


async def check_alerts(session: AsyncSession) -> list[Alert]:
    now = datetime.now(timezone.utc)
    stuck_min = float(await get_setting(session, "alert_stuck_minutes") or 5)
    out: list[Alert] = []
    stuck = (await session.execute(select(func.count()).select_from(WebhookEvent).where(
        WebhookEvent.status.in_(("queued", "processing")), WebhookEvent.received_at < now - timedelta(minutes=stuck_min)))).scalar_one()
    if stuck:
        out.append(Alert("events_stuck", f"{stuck} incoming message(s) waiting more than {stuck_min:g} min: is the worker running?"))
    limit = int(await get_setting(session, "alert_failed_sends_per_hour") or 5)
    failed = (await session.execute(select(func.count()).select_from(Message).where(
        Message.created_at >= now - timedelta(hours=1), Message.delivery_status == "failed"))).scalar_one()
    if failed > limit:
        out.append(Alert("failed_sends", f"{failed} replies failed to send in the last hour (limit {limit}). Check the channel token."))
    shared, asked = await _card_events(session, now - ALERT_REPEAT)
    if shared:
        out.append(Alert("card_shared", f"{shared} guest message(s) in the last hour contained a card number. It was hidden "
                                        "and the guest was warned; check the Inbox."))
    if asked:
        out.append(Alert("card_request", f"{asked} AI reply(s) in the last hour asked for card details and were replaced. "
                                         "Check the Inbox and the knowledge base."))
    b = await budget_state(session)
    if b.level in ("alert", "exceeded"):
        out.append(Alert(f"budget_{b.level}", f"AI budget {b.level}: {b.percent:.0f}% of ${float(b.budget):g} used this month."))
    return out


async def send_alerts(session: AsyncSession, alerts: list[Alert], *, client: httpx.AsyncClient | None = None) -> list[str]:
    """Post new alerts (one per issue per hour) to the webhook. Returns the keys actually sent."""
    url = (await get_setting(session, "alert_webhook_url") or "").strip()
    if not url or not alerts:
        return []
    now = datetime.now(timezone.utc)
    last: dict = dict(await get_setting(session, "alert_last_sent") or {})
    sent: list[str] = []
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        for a in alerts:
            prev = last.get(a.key)
            if prev and now - datetime.fromisoformat(prev) < ALERT_REPEAT:
                continue
            try:
                r = await client.post(url, json={"text": f"⚠️ AI assistant: {a.text}"})
                r.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - an unreachable webhook must not break the cron
                logger.warning("alert webhook failed for %s: %s", a.key, exc)
                continue
            last[a.key] = now.isoformat()
            sent.append(a.key)
    finally:
        if own:
            await client.aclose()
    if sent:
        await set_setting(session, "alert_last_sent", last)
        await session.commit()
    return sent
