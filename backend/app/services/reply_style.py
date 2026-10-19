"""Question types and the reply style an admin sets for each (AI settings -> Reply styles).

classify(): which of TYPES a turn is, from how it was answered (path / program match) and its words. pre_classify()
is the same guess from the words alone, used before the answer exists (to pick the tone for the AI).

Per type (setting "reply_styles"; every field optional, empty = no change):
- tone       added to the AI's instructions for that type (AI-written answers only; saved answers are used as written)
- template   wraps the answer; must contain {answer}  (template_en for English-speaking guests)
- followup   a short extra message bubble after the answer  (followup_en)
- handover   "none" | "offer" (add the handover offer) | "auto" (hand the chat to a person)
The guest's language picks the Bangla or English text (Banglish reads Bangla).
"""
from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.programs import _DATE, _FEE, _GENERIC, _LIST, _PLACE, _is_intent
from app.rag.search import norm
from app.services.language import detect, localized
from app.services.settings_service import get_setting

TYPES = ("greeting", "general", "org_info", "program_date", "program_fee", "program_location", "enrolment", "unknown",
         "handover")
PROGRAM_TYPES = ("program_date", "program_fee", "program_location")
HANDOVER_RULES = ("none", "offer", "auto")
LABELS = {"greeting": "Greeting", "general": "General question", "org_info": "Organization information",
          "program_date": "Program date / schedule", "program_fee": "Program fee", "program_location": "Program location",
          "enrolment": "Enrolment / callback", "unknown": "Unknown question", "handover": "Human handover"}

_ENROL = re.compile(r"(?i)(bhorti\s+hote|bhorti\s+hobo|join\s+korte|enrol|enroll|register\s+(me|korte)|registration\s+korte|"
                    r"call\s+(me|korben|dite)|callback|call\s*back|phone\s+korben|ভর্তি\s+হতে|ভর্তি\s+হব|রেজিস্ট্রেশন\s+করতে|"
                    r"কল\s+(দিন|করবেন|করুন)|ফোন\s+(দিন|করবেন|করুন)|যোগ\s+দিতে)")
_PATH_TYPE = {"greeting": "greeting", "smalltalk": "general", "handover": "handover", "router_handover": "handover",
              "tool": "enrolment", "tool_confirm": "enrolment", "no_context": "unknown", "llm_unavailable": "unknown"}


def _program_words(text: str) -> tuple[bool, bool, bool, bool]:
    tokens = norm(text).split()
    return (any(_is_intent(t, _DATE) for t in tokens), any(_is_intent(t, _FEE) for t in tokens),
            any(_is_intent(t, _PLACE) for t in tokens), any(_is_intent(t, _LIST | _GENERIC) for t in tokens))


def is_program_related(text: str) -> bool:
    """A question about a program, course or event (and not, say, a general one)."""
    return _program_words(text)[3]


def pre_classify(text: str) -> str:
    from app.services.pipeline import is_org_specific
    if _ENROL.search(text or ""):
        return "enrolment"
    date, fee, place, program = _program_words(text)
    if program and (date or fee or place):
        return "program_date" if date else "program_fee" if fee else "program_location"
    return "org_info" if is_org_specific(text) else "general"


def classify(text: str, path: str | None, debug: dict | None = None) -> str:
    d = debug or {}
    if path in _PATH_TYPE:
        return _PATH_TYPE[path]
    pm = d.get("program_match")
    if path == "program" and pm:  # a question asking nothing in particular "wants" everything: date first
        return "program_date" if pm.get("wants_date") else "program_fee" if pm.get("wants_fee") else "program_location"
    if path in ("lead", "data_question"):
        return "general"
    return pre_classify(text)


async def styles(session: AsyncSession) -> dict:
    return dict(await get_setting(session, "reply_styles") or {})


async def tone_for(session: AsyncSession, qtype: str) -> str:
    return ((await styles(session)).get(qtype) or {}).get("tone", "").strip()


def _lang(text: str) -> str:
    return "en" if detect(text or "") == "en" else "bn"


def _pick(style: dict, key: str, lang: str) -> str:
    return ((style.get(f"{key}_en") if lang == "en" else None) or style.get(key) or "").strip()


async def apply_style(session: AsyncSession, qtype: str, question: str, answer: str) -> tuple[str, list[str], str]:
    """-> (answer as the guest gets it, extra bubbles, handover rule). Never empties an answer."""
    style = (await styles(session)).get(qtype) or {}
    lang = _lang(question)
    tpl = _pick(style, "template", lang)
    text = tpl.replace("{answer}", answer) if answer and "{answer}" in tpl else answer
    extra = [x for x in (_pick(style, "followup", lang),) if x]
    rule = style.get("handover") if style.get("handover") in HANDOVER_RULES else "none"
    if rule == "offer" and qtype != "handover":
        extra.append(await localized(session, "handover_offer", question, lang))
    return text, extra, rule
