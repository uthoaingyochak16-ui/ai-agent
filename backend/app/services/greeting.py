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

from app.models import ChannelIdentity, Message
from app.services.greeting_words import GREETING_EMOJI, REPLY_OPENINGS
from app.services.language import detect
from app.services.settings_service import get_setting

# Words that may accompany a greeting without making it a question ("hi bhai", "hello sir", "salam Quantum team").
_FILLER = {"there", "dear", "all", "bhai", "vai", "bhaiya", "vaiya", "apu", "apa", "sir", "madam", "mam", "maam", "ji",
           "jee", "dada", "didi", "boss", "brother", "sister", "friend", "everyone", "everybody", "team", "admin",
           "guys", "quantum", "foundation", "method", "page", "respected", "honourable", "honorable",
           "ভাই", "ভাইয়া", "আপু", "আপা", "স্যার", "ম্যাডাম", "জি", "দাদা", "দিদি", "সবাইকে", "সবাই", "কোয়ান্টাম",
           "ফাউন্ডেশন", "মেথড", "টিম", "এডমিন", "প্রিয়", "শ্রদ্ধেয়"}
_MAX_TOKENS = 12


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())  # punctuation and emoji -> space; keep Bangla vowel signs
    s = "".join(c if c.isspace() or unicodedata.category(c)[0] in "LMN" else " " for c in s)
    return re.sub(r"(\w)\1+", r"\1", s)        # "hiii" / "hellooo" -> "hi" / "helo"


def is_greeting(text: str, words: list[str]) -> bool:
    """The whole message is greetings and courtesy words: one ("salam"), several ("hi, assalamu alaikum, good
    morning sir") or a hello emoji on its own (👋)."""
    if (text or "").strip() and all(ch in "".join(GREETING_EMOJI) or ch.isspace() for ch in text.strip()):
        return True
    vocab = {_norm(w).replace(" ", "") for w in words}
    if _norm(text).replace(" ", "") in vocab:   # a listed phrase made of courtesy words itself ("Dear Sir")
        return True
    tokens = [t for t in _norm(text).split() if t not in {_norm(f) for f in _FILLER}]
    if not tokens or len(tokens) > _MAX_TOKENS:
        return False
    # split the words into consecutive greetings: "good morning" + "salam", "assalamu alaikum" + "hi"
    ok = [True] + [False] * len(tokens)
    for end in range(1, len(tokens) + 1):
        ok[end] = any(ok[start] and "".join(tokens[start:end]) in vocab for start in range(max(0, end - 8), end))
    return ok[-1]


def _opening(text: str) -> tuple[str, str] | None:
    """The guest's own greeting (Bangla, English) when it is answered in kind instead of with the salam."""
    joined = _norm(text).replace(" ", "")
    for words, bn, en in REPLY_OPENINGS:
        if any(_norm(w).replace(" ", "") in joined for w in words):
            return bn, en
    return None


async def greeting_reply(session: AsyncSession, r, text: str, *, assume_greeting: bool = False) -> str | None:
    """assume_greeting: the intent router already decided it is a greeting the word list does not know."""
    if not text or not await get_setting(session, "greeting_enabled"):
        return None
    if not assume_greeting and not is_greeting(text, list(await get_setting(session, "greeting_words") or [])):
        return None
    key = await _which_reply(session, r)
    # formal, always; in English only for a guest who has been writing English ("hi" alone is not enough: most
    # Bangladeshi guests open with "hi" / "hello" and then write Bangla or Banglish)
    lang = "en" if await _writes_english(session, r) else "bn"
    reply = str((await get_setting(session, f"{key}_en") if lang == "en" else None) or await get_setting(session, key))
    opening = _opening(text)
    if opening and reply.startswith(_SALAM[lang]):
        reply = (opening[1] if lang == "en" else opening[0]) + reply[len(_SALAM[lang]):]
    return reply


_SALAM = {"bn": "আসসালামু আলাইকুম", "en": "Assalamu Alaikum"}


async def _which_reply(session: AsyncSession, r) -> str:
    prev = r.prev_user_message_at
    if prev is None:
        # first message on this channel; still a returning customer if another linked channel has talked to us
        other = (await session.execute(select(ChannelIdentity.last_user_message_at).where(
            ChannelIdentity.profile_id == r.profile.id, ChannelIdentity.id != r.identity.id,
            ChannelIdentity.last_user_message_at.is_not(None)).order_by(ChannelIdentity.last_user_message_at.desc())
            .limit(1))).scalar_one_or_none()
        if other is None:
            return "greeting_first_reply"
        prev = other
    gap = timedelta(hours=float(await get_setting(session, "greeting_gap_hours")))
    if r.identity.last_user_message_at - prev >= gap:
        return "greeting_return_reply"
    return "greeting_repeat_reply"  # "hi" again soon after: short, never "I don't know"


async def _writes_english(session: AsyncSession, r) -> bool:
    if r.conversation is None:
        return False
    rows = (await session.execute(select(Message.content_redacted).where(
        Message.conversation_id == r.conversation.id, Message.role == "user").order_by(Message.id.desc()).limit(6))).scalars().all()
    real = [t for t in rows if t and len(t.split()) >= 3]   # a few real sentences, not the greeting itself
    return bool(real) and all(detect(t) == "en" for t in real[:3])
