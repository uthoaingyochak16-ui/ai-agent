"""WhatsApp broadcast: one approved template message to many guests (program news, reminders).

Rules (WhatsApp's and ours): only an approved template may start a conversation, so a broadcast always sends one; only
guests who wrote to us on WhatsApp and gave consent to be contacted are in the audience (erased profiles never);
the worker sends a few per second and stops when staff cancel. Every sent message lands in the guest's conversation,
so replies come back to the Inbox and the assistant answers them as usual.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.base import OutOfWindowError, TemplateMessage
from app.models import Broadcast, BroadcastRecipient, ChannelIdentity, Conversation, Profile
from app.services.outbound import SendFailed, send_to_conversation

logger = logging.getLogger(__name__)
BATCH = 20
PAUSE_S = 0.25          # ~4 messages a second: far below WhatsApp's limits


def _audience_q(audience: dict):
    q = (select(ChannelIdentity.id).join(Profile, ChannelIdentity.profile_id == Profile.id)
         .where(ChannelIdentity.channel == "whatsapp", Profile.consent_given.is_(True), Profile.erased_at.is_(None),
                ~ChannelIdentity.external_id.like("test:%")))
    if audience.get("account_id"):
        q = q.where(ChannelIdentity.account_id == audience["account_id"])
    if audience.get("active_days"):
        q = q.where(ChannelIdentity.last_user_message_at >= datetime.now(timezone.utc) - timedelta(days=int(audience["active_days"])))
    return q


async def count_audience(session: AsyncSession, audience: dict) -> int:
    return (await session.execute(select(func.count()).select_from(_audience_q(audience).subquery()))).scalar_one()


async def start(session: AsyncSession, b: Broadcast) -> int:
    ids = (await session.execute(_audience_q(b.audience or {}))).scalars().all()
    for i in ids:
        session.add(BroadcastRecipient(broadcast_id=b.id, identity_id=i))
    b.total, b.status, b.started_at = len(ids), "sending" if ids else "done", datetime.now(timezone.utc)
    if not ids:
        b.finished_at = b.started_at
    return len(ids)


async def run(maker: async_sessionmaker[AsyncSession], broadcast_id: int) -> dict:
    """Send the queued messages of one broadcast (the worker job)."""
    while True:
        async with maker() as session:
            b = await session.get(Broadcast, broadcast_id)
            if b is None or b.status != "sending":
                return {"status": b.status if b else "missing"}
            batch = (await session.execute(select(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id == b.id, BroadcastRecipient.status == "queued").limit(BATCH))).scalars().all()
            if not batch:
                b.status, b.finished_at = "done", datetime.now(timezone.utc)
                await session.commit()
                return {"status": "done", "sent": b.sent, "failed": b.failed}
            tpl = TemplateMessage(name=b.template, language=b.language, body_params=[str(x) for x in b.params or []])
            for r in batch:
                conv = (await session.execute(select(Conversation).where(Conversation.channel_identity_id == r.identity_id)
                                              .order_by(Conversation.id.desc()).limit(1))).scalar_one_or_none()
                if conv is None:
                    r.status, r.error = "skipped", "no conversation with this guest yet"
                    continue
                try:
                    await send_to_conversation(session, conv, b.preview or f"[{b.template}]", template=tpl, sender_role="assistant",
                                               sources={"path": "broadcast", "broadcast_id": b.id})
                    r.status, r.sent_at = "sent", datetime.now(timezone.utc)
                    b.sent += 1
                except (SendFailed, OutOfWindowError) as exc:
                    r.status, r.error = "failed", str(exc)[:300]
                    b.failed += 1
                await session.commit()
                await asyncio.sleep(PAUSE_S)
