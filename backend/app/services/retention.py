"""Conversation retention: chat history older than `conversation_retention_days` is deleted.

Kept on purpose: profiles, their saved details (profile_facts; source_message_id is SET NULL), channel identities
(account id, name, photo) and phone/email - so a returning customer is still recognised and their info remains.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, LLMCall, Message, WebhookEvent
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)


async def purge_old_conversations(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    days = int(await get_setting(session, "conversation_retention_days") or 30)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    msgs = (await session.execute(delete(Message).where(Message.created_at < cutoff))).rowcount
    # conversations with no activity since the cutoff (any messages left in them cascade)
    convs = (await session.execute(delete(Conversation).where(
        func.coalesce(Conversation.last_message_at, Conversation.started_at) < cutoff))).rowcount
    # the raw webhook inbox holds unredacted message text
    events = (await session.execute(delete(WebhookEvent).where(
        WebhookEvent.received_at < cutoff, WebhookEvent.status.in_(("done", "failed"))))).rowcount
    # keep LLM cost rows, drop the prompt audit (it quotes the conversation)
    prompts = (await session.execute(update(LLMCall).where(
        LLMCall.created_at < cutoff, LLMCall.request_json.is_not(None)).values(request_json=None))).rowcount
    await session.commit()
    out = {"messages": msgs, "conversations": convs, "webhook_events": events, "llm_prompts": prompts}
    if any(out.values()):
        logger.info("retention purge (%s days): %s", days, out)
    return out
