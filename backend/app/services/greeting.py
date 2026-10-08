"""Canned replies to a bare greeting ("hi", "hello", "salam"...): no retrieval, no LLM call.

- the customer's very first message ever           -> greeting_first_reply (introduces the AI assistant)
- first message after a gap of greeting_gap_hours  -> greeting_return_reply (no introduction)
- another greeting inside the gap                  -> greeting_repeat_reply (short "yes, go ahead")
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelIdentity
from app.services.settings_service import get_setting

# Words that may accompany a greeting without making it a question ("hi bhai", "hello sir").
_FILLER = {"there", "dear", "all", "bhai", "vai", "bhaiya", "vaiya", "apu", "apa", "sir", "madam", "mam", "ji",
           "ভাই", "ভাইয়া", "আপু", "স্যার", "জি", "everyone", "team", "admin"}
_MAX_TOKENS = 6


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())  # punctuation and emoji -> space; keep Bangla vowel signs
    s = "".join(c if c.isspace() or unicodedata.category(c)[0] in "LMN" else " " for c in s)
    return re.sub(r"(\w)\1+", r"\1", s)        # "hiii" / "hellooo" -> "hi" / "helo"


def is_greeting(text: str, words: list[str]) -> bool:
    tokens = [t for t in _norm(text).split() if t not in {_norm(f) for f in _FILLER}]
    if not tokens or len(tokens) > _MAX_TOKENS:
        return False
    vocab = {_norm(w).replace(" ", "") for w in words}
    return "".join(tokens) in vocab or all(t in vocab for t in tokens)


async def greeting_reply(session: AsyncSession, r, text: str) -> str | None:
    if not text or not await get_setting(session, "greeting_enabled"):
        return None
    if not is_greeting(text, list(await get_setting(session, "greeting_words") or [])):
        return None
    prev = r.prev_user_message_at
    if prev is None:
        # first message on this channel; still a returning customer if another linked channel has talked to us
        other = (await session.execute(select(ChannelIdentity.last_user_message_at).where(
            ChannelIdentity.profile_id == r.profile.id, ChannelIdentity.id != r.identity.id,
            ChannelIdentity.last_user_message_at.is_not(None)).order_by(ChannelIdentity.last_user_message_at.desc())
            .limit(1))).scalar_one_or_none()
        if other is None:
            return str(await get_setting(session, "greeting_first_reply"))
        prev = other
    gap = timedelta(hours=float(await get_setting(session, "greeting_gap_hours")))
    if r.identity.last_user_message_at - prev >= gap:
        return str(await get_setting(session, "greeting_return_reply"))
    return str(await get_setting(session, "greeting_repeat_reply"))  # "hi" again soon after: short, never "I don't know"
