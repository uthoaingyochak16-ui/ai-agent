"""Ready answers for everyday small talk ("who are you?", "how are you?", "kemon achen", "ধন্যবাদ"...): no retrieval,
no LLM call. The list lives in the `smalltalk` setting (AI Settings -> Messages), so staff can edit or extend it.

Matching is deliberately strict - short messages only, exact or near-exact to one of the listed phrasings - so that a
real question ("how are you different from X?") still reaches the knowledge base.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.language import detect
from app.services.settings_service import get_setting

MAX_TOKENS = 8
FUZZY_MIN = 0.86
# polite extras that don't change the meaning ("who are you bhai?", "kemon achen apu")
_EXTRA = {"bhai", "vai", "bhaiya", "vaiya", "apu", "apa", "sir", "madam", "mam", "ji", "please", "plz", "pls", "dear",
          "ভাই", "ভাইয়া", "আপু", "স্যার", "জি", "প্লিজ"}


def _letters_only(s: str) -> str:
    """Punctuation/emoji -> space. Keeps combining marks, which Bangla vowel signs (ি া ে ়) are; regex word chars drop them."""
    s = unicodedata.normalize("NFC", s.lower())
    return "".join(c if c.isspace() or unicodedata.category(c)[0] in "LMN" else " " for c in s)


def _norm(s: str) -> str:
    s = _letters_only(s)
    s = re.sub(r"(\w)\1{2,}", r"\1", s)  # "hellooo" -> "hello", "uuu" -> "u" (keeps real double letters)
    return " ".join(t for t in s.split() if t not in _EXTRA)


def _strip_greeting(text: str, greeting_words: list[str]) -> str:
    """'hi, how are you?' -> 'how are you'."""
    words = sorted({_norm(w) for w in greeting_words if w.strip()}, key=len, reverse=True)
    for w in words:
        if text == w:
            return ""
        if text.startswith(w + " "):
            return text[len(w) + 1:]
    return text


def match(text: str, items: list[dict], greeting_words: list[str] = ()) -> str | None:
    item = match_item(text, items, greeting_words)
    return (item.get("answer") or "").strip() if item else None


def match_item(text: str, items: list[dict], greeting_words: list[str] = ()) -> dict | None:
    t = _strip_greeting(_norm(text), list(greeting_words))
    if not t or len(t.split()) > MAX_TOKENS:
        return None
    best, best_score = None, 0.0
    for item in items:
        answer = (item.get("answer") or "").strip()
        if not answer:
            continue
        for q in item.get("questions") or []:
            nq = _norm(q)
            if not nq:
                continue
            if nq == t:
                return item
            if min(len(nq), len(t)) >= 6:
                score = SequenceMatcher(None, nq, t).ratio()
                if score > best_score:
                    best, best_score = item, score
    return best if best_score >= FUZZY_MIN else None


async def smalltalk_reply(session: AsyncSession, text: str) -> str | None:
    if not text or not await get_setting(session, "smalltalk_enabled"):
        return None
    items = await get_setting(session, "smalltalk") or []
    item = match_item(text, list(items), list(await get_setting(session, "greeting_words") or []))
    if not item:
        return None
    if detect(text) == "en" and (item.get("answer_en") or "").strip():  # reply in the guest's language
        return item["answer_en"].strip()
    return item["answer"].strip()
