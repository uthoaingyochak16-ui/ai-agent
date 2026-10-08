"""Pick ONE reply language per message, so canned replies are never "English + Bangla" at once.

Bangla script -> bn. Latin script with common Banglish words ("koto", "kivabe", "ache") -> bn as well: those guests
read Bangla. Anything else in Latin script -> en.
"""
from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting

_BANGLISH = {
    "ami", "apni", "apnar", "amar", "tumi", "tomar", "apnara", "apnader", "amra", "amader", "ki", "koto", "kto",
    "kivabe", "kibhabe", "keno", "kno", "kothay", "kotha", "kobe", "kokhon", "ache", "ase", "achhe", "achen", "acho",
    "hobe", "hoy", "hoi", "hoye", "na", "nai", "nei", "valo", "bhalo", "kemon", "korbo", "korte", "korben", "kori",
    "koren", "chai", "jante", "janan", "janaben", "bolen", "bolun", "dam", "taka", "tk", "dite", "diye", "nite", "nibo",
    "parbo", "parben", "pari", "jabe", "jai", "ar", "er", "theke", "kaj", "bhai", "vai", "apu", "dhonnobad", "accha",
    "thik", "kono", "shob", "sob", "ekta", "ekti", "kichu", "kisu", "lagbe", "khabar", "kortesi", "korchi",
    "kinbo", "kinte", "pabo", "paben", "jabo", "dibo", "debo", "den", "din", "deben", "ashbo", "asbo", "shikhte", "shekhay",
}
_LATIN_WORD = re.compile(r"[a-zA-Z]+")


def detect(text: str) -> str:
    if any("ঀ" <= ch <= "৿" for ch in text or ""):
        return "bn"
    words = [w.lower() for w in _LATIN_WORD.findall(text or "")]
    if not words:
        return "bn"
    return "bn" if any(w in _BANGLISH for w in words) else "en"


async def localized(session: AsyncSession, key: str, text: str) -> str:
    """The `key` setting (Bangla), or its `key_en` twin when the guest writes English and an English text exists."""
    if detect(text) == "en":
        en = await get_setting(session, f"{key}_en")
        if en:
            return str(en)
    return str(await get_setting(session, key))
