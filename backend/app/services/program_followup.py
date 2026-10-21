"""Program follow-up: after answering a program / course / event question, ask (once, optionally) which district the
guest lives in, and point them to an upcoming program there.

- after_answer: only after a program-related question, never after a general one. Sent as its own message bubble.
  Not asked when the profile already has a location, and at most once per conversation (Conversation.district_asked_at).
  A guest whose location is known gets the nearby suggestion instead, if there is a verified one (also once).
- before_answer: the guest's next message. A district (any script, see rag/districts.py) is saved to the profile's
  location (consent rules apply) and answered with the programs whose place is in that district. Exact districts
  only: a neighbouring district is never guessed. Anything else is handled as a normal message; we never ask again.
- Never asks for a phone number.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProfileFact
from app.rag.districts import district_name, find_districts
from app.rag.programs import format_dates, programs_in_district, quoted, upcoming
from app.services import consent as consent_svc
from app.services.language import style
from app.services.reply_style import PROGRAM_TYPES, is_program_related
from app.services.settings_service import get_setting

MAX_SUGGESTIONS = 3
MAX_ANSWER_WORDS = 8   # "ami Rajshahi thaki" is an answer; a longer message is a new question


def _lang(p, text: str) -> str:
    """The district reply has no language of its own ("Dhaka"): the guest's known preference, else Bangla."""
    s = style(text or "")
    if s == "en":
        return "en"
    return "en" if s is None and getattr(p, "language", None) == "en" else "bn"


async def _known_location(session: AsyncSession, p) -> str | None:
    if p.location:
        return p.location
    held = (await session.execute(select(ProfileFact.id).where(ProfileFact.profile_id == p.id,
                                                               ProfileFact.key == "pending_location"))).first()
    return "(held until consent)" if held else None


async def _suggestions(session: AsyncSession, district: str, lang: str, skip_text: str = "") -> str | None:
    """The upcoming programs in the district as one message: the template for a single one, a short list for several.
    skip_text: the answer just sent; programs it already names are not repeated."""
    found = [p for p in programs_in_district(await upcoming(session), district) if p.name not in skip_text][:MAX_SUGGESTIONS]
    if not found:
        return None
    key = "program_nearby_reply_en" if lang == "en" else "program_nearby_reply"
    if len(found) == 1:
        tpl = (await get_setting(session, key)) or await get_setting(session, "program_nearby_reply")
        return _fill(tpl, found[0], lang)
    intro = await _text(session, "program_nearby_list_intro", lang)
    return "\n".join([intro, *(_fill("• {program_name} — {date}, {place}", p, lang) for p in found)])


def _fill(tpl: str, p, lang: str) -> str:
    when = format_dates(p.start_date, p.end_date, lang)
    for k, v in (("program_name", quoted(p.name)), ("place", p.place or ""), ("date", when)):
        tpl = tpl.replace("${" + k + "}", v).replace("{" + k + "}", v)
    return tpl.replace(", —", " —").replace(", .", ".").rstrip(", ")


async def _text(session: AsyncSession, key: str, lang: str) -> str:
    return ((await get_setting(session, f"{key}_en")) if lang == "en" else None) or await get_setting(session, key)


async def after_answer(session: AsyncSession, r, question: str, qtype: str, answer: str = "") -> list[str]:
    """Extra bubbles after a program-related answer: the district question, or a nearby program for a known district
    (one the answer did not already name)."""
    conv, p = r.conversation, r.profile
    if not await get_setting(session, "program_followup_enabled") or conv.district_asked_at is not None or p.erased_at:
        return []
    if qtype not in PROGRAM_TYPES and not is_program_related(question):
        return []
    lang = _lang(p, question)
    known = await _known_location(session, p)
    conv.district_asked_at = datetime.now(timezone.utc)  # once per conversation, whichever of the two it is
    if known:
        districts = find_districts(known)
        hint = await _suggestions(session, districts[0], lang, answer) if districts else None
        if hint:
            r.events.append(f"nearby program suggested for the known district {districts[0]}")
            return [hint]
        return []
    conv.district_awaiting = True
    r.events.append("asked for the guest's district (program follow-up)")
    return [await _text(session, "program_district_prompt", lang)]


async def before_answer(session: AsyncSession, r, text: str) -> str | None:
    """The message after the district question. Returns the reply when it named a district, else None (go on)."""
    conv, p = r.conversation, r.profile
    if not conv.district_awaiting:
        return None
    conv.district_awaiting = False  # one chance: whatever they write next, we do not ask again
    districts = find_districts(text)
    if not districts or len((text or "").split()) > MAX_ANSWER_WORDS:
        r.events.append("district question not answered: carry on")
        return None
    district = districts[0]
    lang = _lang(p, text)
    status = await consent_svc.apply_field(session, p, "location", district_name(district, "bn"))
    r.events.append(f"district {district}: location {status}")
    return await _suggestions(session, district, lang) or await _text(session, "program_nearby_none", lang)
