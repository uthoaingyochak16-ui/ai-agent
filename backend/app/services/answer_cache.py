"""Safety rules for the shared semantic cache (one guest's cached answer is served to everyone who asks the same).

- A hit needs the same TOPIC words, not just a close vector: "fee koto?" must never be served for
  "yoga course fee koto?" (same check as the Q&A coverage rule: content words, organisation names ignored).
- status: "auto" (made by the AI; expires with cache_ttl_days / context_version, blocked by negative feedback),
  "verified" (an admin approved it: survives knowledge changes, still needs a matching question), "blocked" (never served).
- Admin feedback ("answer is wrong") blocks every entry with that exact answer; changing a Variable removes entries
  that quote its old value.
"""
from __future__ import annotations

import re

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SemanticCache
from app.rag.search import _matches, content_words
from app.rag.variables import NEUTRAL_WORDS

STATUSES = ("auto", "verified", "blocked")


def topic_words(text: str) -> set[str]:
    return {w for w in content_words(text) if w not in NEUTRAL_WORDS}


def same_topic(a: str, b: str) -> bool:
    """Every topic word of each question is found (with synonyms / inflections) in the other one."""
    wa, wb = topic_words(a), topic_words(b)
    if not wa or not wb:  # nothing to compare ("ki?"): never trust a vector match alone
        return False
    return all(_matches(w, wb) for w in wa) and all(_matches(w, wa) for w in wb)


async def block_answer(session: AsyncSession, answer: str) -> int:
    """Negative feedback: block every cached copy of this exact answer. Returns how many."""
    text = (answer or "").strip()
    if not text:
        return 0
    res = await session.execute(update(SemanticCache).where(
        SemanticCache.answer == text, SemanticCache.status != "blocked").values(status="blocked"))
    return res.rowcount or 0


async def forget_value(session: AsyncSession, old_value: str | None) -> int:
    """A Variable changed: cached answers quoting the old value are wrong now, whatever their status."""
    old = (old_value or "").strip()
    if len(old) < 2:  # a one-character value would match half the cache
        return 0
    pattern = re.compile(rf"(?<!\w){re.escape(old)}(?!\w)")
    ids = [c.id for c in (await session.execute(select(SemanticCache).where(SemanticCache.answer.contains(old)))).scalars()
           if pattern.search(c.answer)]
    if ids:
        await session.execute(delete(SemanticCache).where(SemanticCache.id.in_(ids)))
    return len(ids)
