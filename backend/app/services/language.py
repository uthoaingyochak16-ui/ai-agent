"""Pick ONE reply language per message, so canned replies are never "English + Bangla" at once.

Bangla script -> bn. Latin script with common Banglish words ("koto", "kivabe", "ache") -> bn as well: those guests
read Bangla. Anything else in Latin script -> en.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message
from app.services.settings_service import get_setting

_BANGLISH = {
    "ami", "apni", "apnar", "amar", "tumi", "tomar", "apnara", "apnader", "amra", "amader", "ki", "koto", "kto",
    "kivabe", "kibhabe", "keno", "kno", "kothay", "kotha", "kobe", "kokhon", "ache", "ase", "achhe", "achen", "acho",
    "hobe", "hoy", "hoi", "hoye", "na", "nai", "nei", "valo", "bhalo", "kemon", "korbo", "korte", "korben", "kori",
    "koren", "chai", "jante", "janan", "janaben", "bolen", "bolun", "dam", "taka", "tk", "dite", "diye", "nite", "nibo",
    "parbo", "parben", "pari", "jabe", "jai", "ar", "er", "theke", "kaj", "bhai", "vai", "apu", "dhonnobad", "accha",
    "thik", "kono", "shob", "sob", "ekta", "ekti", "kichu", "kisu", "lagbe", "khabar", "kortesi", "korchi",
    "kinbo", "kinte", "pabo", "paben", "jabo", "dibo", "debo", "den", "din", "deben", "ashbo", "asbo", "shikhte", "shekhay",
    "ji", "jee", "jii", "hya", "hae", "haa", "ha", "hoo", "acha", "achha",
}
_LATIN_WORD = re.compile(r"[a-zA-Z]+")


def detect(text: str) -> str:
    if any("ঀ" <= ch <= "৿" for ch in text or ""):
        return "bn"
    words = [w.lower() for w in _LATIN_WORD.findall(text or "")]
    if not words:
        return "bn"
    return "bn" if any(w in _BANGLISH for w in words) else "en"


def detect_in_conversation(text: str, history: list[tuple[str, str]]) -> str:
    """A reply too short to tell ("Ok", "yes", "hmm") keeps the language the guest has been writing in."""
    if any("ঀ" <= ch <= "৿" for ch in text or "") or len(_LATIN_WORD.findall(text or "")) > 3:
        return detect(text)
    for role, turn in reversed(history or []):
        if role == "user" and (any("ঀ" <= ch <= "৿" for ch in turn) or len(_LATIN_WORD.findall(turn)) > 3):
            return detect(turn)
    return detect(text)


async def localized(session: AsyncSession, key: str, text: str, lang: str | None = None) -> str:
    """The `key` setting (Bangla), or its `key_en` twin when the guest writes English and an English text exists.
    `lang` (e.g. from the intent router: bn | en | banglish) overrides the detection."""
    if (lang or detect(text)) == "en":
        en = await get_setting(session, f"{key}_en")
        if en:
            return str(en)
    return str(await get_setting(session, key))


# ---- the guest's preferred language, kept on the profile ------------------------------------------------------------
PREFERENCE_MIN_MESSAGES = 3   # decide only after this many real messages
PREFERENCE_WINDOW = 10        # ...looking at the latest this many
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")


def style(text: str) -> str | None:
    """bn (Bangla script) | banglish (Bangla in Latin letters) | en; None for "ok", "hello", a phone number..."""
    t = _PLACEHOLDER.sub(" ", text or "")
    if any("ঀ" <= ch <= "৿" for ch in t):
        return "bn"
    words = [w.lower() for w in _LATIN_WORD.findall(t)]
    if any(w in _BANGLISH for w in words):
        return "banglish"
    return "en" if len(words) >= 3 else None  # too short to tell English from a name or a greeting


async def learn_preference(session: AsyncSession, profile) -> str | None:
    """Set profile.language from the guest's own messages (the majority of the latest ones, once there are enough).
    Follows the guest if they switch for good. Returns the new value when it changed."""
    if profile.erased_at:
        return None
    rows = (await session.execute(select(Message.content_redacted).join(Conversation).where(
        Conversation.profile_id == profile.id, Message.role == "user").order_by(Message.id.desc())
        .limit(PREFERENCE_WINDOW * 3))).scalars().all()
    seen = [x for x in (style(t) for t in rows) if x][:PREFERENCE_WINDOW]
    if len(seen) < PREFERENCE_MIN_MESSAGES:
        return None
    lang, n = Counter(seen).most_common(1)[0]
    if n * 2 <= len(seen) or profile.language == lang:  # no clear majority, or nothing new
        return None
    profile.language = lang
    profile.summary_dirty = True
    return lang
