from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.db.session import get_session
from app.llm.service import budget_state
from app.models import ChannelIdentity, Conversation, LLMCall, Message, UnansweredQuestion

router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(any_staff)])
REAL = ~ChannelIdentity.external_id.like("test:%")


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/dashboard")
async def dashboard(days: int = Query(14, ge=1, le=180), session: AsyncSession = Depends(get_session)):
    since = _since(days)
    by_ch = (await session.execute(
        select(ChannelIdentity.channel, Message.role, func.count()).select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
        .where(Message.created_at >= since, REAL).group_by(ChannelIdentity.channel, Message.role))).all()
    channels: dict[str, dict] = {}
    for ch, role, n in by_ch:
        channels.setdefault(ch, {"channel": ch, "user": 0, "assistant": 0, "agent": 0})[role] = n
    day = func.date_trunc("day", Message.created_at)
    daily_msgs = dict((d.date().isoformat(), n) for d, n in (await session.execute(
        select(day, func.count()).select_from(Message).join(Conversation, Message.conversation_id == Conversation.id)
        .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
        .where(Message.created_at >= since, Message.role == "user", REAL).group_by(day))).all())
    cday = func.date_trunc("day", LLMCall.created_at)
    daily_cost = {d.date().isoformat(): (float(c), int(ti), int(to)) for d, c, ti, to in (await session.execute(
        select(cday, func.sum(LLMCall.cost_usd), func.sum(LLMCall.tokens_in), func.sum(LLMCall.tokens_out))
        .where(LLMCall.created_at >= since).group_by(cday))).all()}
    daily = []
    for i in range(days - 1, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
        c = daily_cost.get(d, (0.0, 0, 0))
        daily.append({"date": d, "messages": daily_msgs.get(d, 0), "cost": c[0], "tokens_in": c[1], "tokens_out": c[2]})
    path = Message.sources_json["path"].astext
    paths = [{"path": p or "unknown", "count": n} for p, n in (await session.execute(
        select(path, func.count()).select_from(Message).join(Conversation, Message.conversation_id == Conversation.id)
        .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
        .where(Message.role == "assistant", Message.created_at >= since, REAL).group_by(path))).all()]
    un_open = (await session.execute(select(func.count()).select_from(UnansweredQuestion).where(UnansweredQuestion.resolved.is_(False)))).scalar_one()
    un_recent = [{"id": u.id, "question": u.question, "channel": u.channel, "reason": u.reason, "created_at": u.created_at}
                 for u in (await session.execute(select(UnansweredQuestion).where(UnansweredQuestion.resolved.is_(False))
                                                 .order_by(UnansweredQuestion.id.desc()).limit(8))).scalars()]
    handover = [{"id": c.id, "channel": i.channel, "reason": c.handover_reason, "last_message_at": c.last_message_at,
                 "name": i.display_name} for c, i in (await session.execute(
        select(Conversation, ChannelIdentity).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
        .where(Conversation.status == "handover", REAL).order_by(Conversation.last_message_at.desc().nullslast()).limit(20))).all()]
    b = await budget_state(session)
    totals = {"messages_in": sum(c["user"] for c in channels.values()), "cost": sum(d["cost"] for d in daily),
              "tokens_in": sum(d["tokens_in"] for d in daily), "tokens_out": sum(d["tokens_out"] for d in daily)}
    return {"days": days, "channels": list(channels.values()), "daily": daily, "paths": paths, "totals": totals,
            "unanswered": {"open": un_open, "recent": un_recent}, "handover": handover,
            "budget": {"spent": float(b.spent), "budget": float(b.budget), "percent": round(b.percent, 1), "level": b.level}}


@router.get("/cost")
async def cost(days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_session)):
    since = _since(days)

    async def grp(*cols):
        rows = (await session.execute(select(*cols, func.count(), func.sum(LLMCall.tokens_in), func.sum(LLMCall.tokens_out),
                                             func.sum(LLMCall.cost_usd), func.avg(LLMCall.latency_ms))
                                      .where(LLMCall.created_at >= since).group_by(*cols).order_by(func.sum(LLMCall.cost_usd).desc()))).all()
        return [{"key": [str(k.date()) if isinstance(k, datetime) else k for k in r[:len(cols)]], "calls": r[-5], "tokens_in": int(r[-4] or 0),
                 "tokens_out": int(r[-3] or 0), "cost": float(r[-2] or 0), "avg_latency_ms": int(r[-1] or 0)} for r in rows]
    return {"by_day": await grp(func.date_trunc("day", LLMCall.created_at)), "by_channel": await grp(LLMCall.channel),
            "by_model": await grp(LLMCall.provider, LLMCall.model), "by_task": await grp(LLMCall.task),
            "errors": (await session.execute(select(func.count()).select_from(LLMCall).where(
                LLMCall.created_at >= since, LLMCall.status == "error"))).scalar_one()}


@router.get("/calls")
async def calls(limit: int = Query(50, le=200), session: AsyncSession = Depends(get_session)):
    """Outgoing-request audit: exactly what was sent to each provider (post-filter)."""
    rows = (await session.execute(select(LLMCall).order_by(LLMCall.id.desc()).limit(limit))).scalars().all()
    return [{"id": c.id, "created_at": c.created_at, "task": c.task, "provider": c.provider, "model": c.model, "status": c.status,
             "error": c.error, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out, "cost": float(c.cost_usd),
             "latency_ms": c.latency_ms, "fallback_used": c.fallback_used, "budget_fallback": c.budget_fallback,
             "is_test": c.is_test, "redactions": c.redactions, "request": c.request_json} for c in rows]


class ResolveIn(BaseModel):
    resolved: bool = True


@router.post("/unanswered/{uid}/resolve")
async def resolve_unanswered(uid: int, body: ResolveIn, session: AsyncSession = Depends(get_session)):
    await session.execute(update(UnansweredQuestion).where(UnansweredQuestion.id == uid).values(resolved=body.resolved))
    await session.commit()
    return {"ok": True}
