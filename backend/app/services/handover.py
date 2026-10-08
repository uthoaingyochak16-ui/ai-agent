from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message


def detect_handover(text: str, keywords: list[str]) -> str | None:
    """Return the matched keyword, or None. Latin keywords match on word boundaries, Bangla as substrings."""
    low = text.lower()
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if k.isascii():
            if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", low):
                return kw
        elif k in low:
            return kw
    return None


async def pause_ai(session: AsyncSession, conv: Conversation, reason: str) -> None:
    conv.ai_paused = True
    conv.ai_paused_at = datetime.now(timezone.utc)
    conv.status = "handover"
    conv.handover_reason = reason[:120]
    await session.flush()


async def resume_ai(session: AsyncSession, conv: Conversation) -> None:
    conv.ai_paused = False
    conv.ai_paused_at = None
    conv.status = "open"
    conv.handover_reason = None
    await session.flush()


async def maybe_auto_resume(session: AsyncSession, conv: Conversation, minutes: float) -> bool:
    """A paused chat goes back to the AI once staff have been quiet for `minutes` (0 = never), so a guest is not
    left without answers because someone replied once from the inbox and forgot to press Resume."""
    if not conv.ai_paused or not minutes:
        return False
    last_staff = (await session.execute(select(func.max(Message.created_at)).where(
        Message.conversation_id == conv.id, Message.role == "agent"))).scalar_one_or_none()
    if conv.ai_paused_at is None and last_staff is None:  # pause time unknown: start the clock now
        conv.ai_paused_at = datetime.now(timezone.utc)
        return False
    since = max(t for t in (conv.ai_paused_at, last_staff) if t is not None)
    if datetime.now(timezone.utc) - since < timedelta(minutes=minutes):
        return False
    await resume_ai(session, conv)
    return True
