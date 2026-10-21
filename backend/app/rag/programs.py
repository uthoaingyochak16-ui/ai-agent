"""Programs table -> answers about upcoming courses and events ("next batch kobe?", "yoga fee koto?", "কোথায় হবে?").

- "Today" is Bangladesh time (UTC+6, no daylight saving). A program is listed until its last day is over, nearest first;
  the next day it is deleted (daily cron + every list call).
- A question counts as a program question only when it asks about a date / fee / place / schedule AND either names a
  program or asks for programs in general ("next program", "upcoming batch"). Anything else ("how long is the course?")
  is left to the knowledge base.
- A word in the question that no candidate program knows ("yoga" for a "Quantum Method Course" row) rules that program
  out, so one program's fee is never given for another.
- The data is handed to the AI as context to analyse (nearest date, comparisons, days left); without an AI model the
  same data is turned into a short answer here. Either way a program's name is always written inside quotation marks
  ("<name>" অনুষ্ঠিত হবে ...), and every name, date, place and fee comes from the table only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Program
from app.rag.search import _matches, content_words, norm
from app.rag.translit import is_bangla, romanize, sound_key
from app.rag.variables import NEUTRAL_WORDS
from app.services.language import detect

BD_TZ = timezone(timedelta(hours=6))
MAX_PROGRAMS = 5

# Common Banglish spellings that a generic sound index cannot infer reliably.
_PROGRAM_ALIASES = {"proga": {"প্রজ্ঞা"}, "progga": {"প্রজ্ঞা"}, "jalali": {"জালালি"}}

_DATE = {"when", "date", "dates", "kobe", "kokhon", "tarikh", "tarik", "shuru", "start", "starts", "schedule", "time",
         "shomoy", "somoy", "কবে", "কখন", "তারিখ", "শুরু", "সময়", "সময়সূচি", "শিডিউল"}
_FEE = {"fee", "fees", "cost", "price", "charge", "khoroch", "dam", "taka", "ফি", "খরচ", "দাম", "মূল্য", "টাকা"}
_PLACE = {"where", "place", "venue", "location", "kothay", "kothai", "kothae", "kuthay", "kotai", "jaiga", "thikana", "address", "কোথায়", "স্থান", "ভেন্যু",
          "জায়গা", "ঠিকানা", "লোকেশন"}
_LIST = {"program", "programs", "programme", "batch", "batches", "upcoming", "next", "porer", "event", "events", "class",
         "classes", "প্রোগ্রাম", "ব্যাচ", "আসন্ন", "পরবর্তী", "পরের", "ইভেন্ট", "ক্লাস", "কর্মসূচি", "আগামী"}
_ALL_INTENT = _DATE | _FEE | _PLACE | _LIST
_FILLER = {"ki", "ki?", "acha", "ase", "ache", "ache?", "ase?", "hobe", "hoy", "hoy?", "bolo", "bolen", "please"}
_GENERIC = {"course", "courses", "class", "classes", "program", "programs", "programme", "batch", "batches", "event", "events",
            "workshop", "seminar", "camp", "session", "kors", "কোর্স", "ক্লাস", "প্রোগ্রাম", "ব্যাচ", "ইভেন্ট"}

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
_BN_MONTHS = ["জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def today_bd(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(BD_TZ).date()


async def purge_expired(session: AsyncSession, today: date | None = None) -> int:
    """Delete programs whose last day is over. Returns how many."""
    res = await session.execute(delete(Program).where(func.coalesce(Program.end_date, Program.start_date) < (today or today_bd())))
    return res.rowcount or 0


async def upcoming(session: AsyncSession, today: date | None = None) -> list[Program]:
    d = today or today_bd()
    return list((await session.execute(select(Program).where(func.coalesce(Program.end_date, Program.start_date) >= d)
                                       .order_by(Program.start_date, Program.id))).scalars())


# ---- formatting ---------------------------------------------------------------------------------------------------
def format_dates(start: date, end: date | None, lang: str) -> str:
    months = _BN_MONTHS if lang == "bn" else _EN_MONTHS
    if end is None or end == start:
        out = f"{start.day} {months[start.month - 1]} {start.year}"
    elif (start.year, start.month) == (end.year, end.month):
        out = f"{start.day}–{end.day} {months[end.month - 1]} {end.year}"
    elif start.year == end.year:
        out = f"{start.day} {months[start.month - 1]} – {end.day} {months[end.month - 1]} {end.year}"
    else:
        out = f"{start.day} {months[start.month - 1]} {start.year} – {end.day} {months[end.month - 1]} {end.year}"
    return out.translate(_BN_DIGITS) if lang == "bn" else out


def days_left_text(p: Program, today: date, lang: str) -> str:
    n = (p.start_date - today).days
    if n > 0:
        return f"আর {n} দিন বাকি".translate(_BN_DIGITS) if lang == "bn" else f"in {n} day{'s' if n != 1 else ''}"
    return "চলছে" if lang == "bn" else "in progress"


def context_line(p: Program, today: date) -> str:
    """One program as the AI sees it (English labels; the AI answers in the guest's language)."""
    parts = [f"{p.name}", f"dates: {format_dates(p.start_date, p.end_date, 'en')} ({p.start_date.isoformat()}"
             + (f" to {p.end_date.isoformat()}" if p.end_date and p.end_date != p.start_date else "") + ")",
             f"starts in {(p.start_date - today).days} day(s)" if p.start_date > today else "already running",
             f"fee: {p.fee}" if p.fee else "fee: not listed", f"place: {p.place}" if p.place else "place: not listed"]
    if p.date_text and p.date_text.strip():
        parts.append(f"date as announced: {p.date_text.strip()[:80]}")
    if p.notes:
        parts.append(f"note: {p.notes[:200]}")
    if p.registration_url:
        parts.append(f"registration link: {p.registration_url}")
    return "PROGRAM " + " | ".join(parts)


# ---- matching -------------------------------------------------------------------------------------------------------
# English intent words written in Bangla script ("নেক্সট" = next, "প্রোগ্রাম" = program), found by sound, so no list of
# Bangla spellings is needed; a real subject word ("ইয়োগা", "হজ") sounds like none of them
_INTENT_SOUNDS = {sound_key(w): w for w in _ALL_INTENT if w.isascii() and len(sound_key(w)) >= 3}


def _canon(token: str) -> str:
    return _INTENT_SOUNDS.get(sound_key(romanize(token)), token) if is_bangla(token) and token not in _ALL_INTENT else token


def _is_intent(token: str, words: set[str]) -> bool:
    token = _canon(token)
    return token in words or (not token.isascii() and len(token) >= 3 and any(not w.isascii() and token.startswith(w) for w in words))


@dataclass
class ProgramQuestion:
    programs: list[Program]
    wants_date: bool
    wants_fee: bool
    wants_place: bool
    generic: bool          # asked for programs in general (no name)


def match_program_question(text: str, programs: list[Program]) -> ProgramQuestion | None:
    if not programs:
        return None
    tokens = norm(text).split()
    wants_date = any(_is_intent(t, _DATE) for t in tokens)
    wants_fee = any(_is_intent(t, _FEE) for t in tokens)
    wants_place = any(_is_intent(t, _PLACE) for t in tokens)
    generic_word = any(_is_intent(t, _LIST) for t in tokens)
    if not (wants_date or wants_fee or wants_place or generic_word):
        return None
    named: list[Program] = []
    asked = set(content_words(text))
    for word in tuple(asked):
        asked.update(_PROGRAM_ALIASES.get(word, ()))
    specific = {a for a in asked if a not in _GENERIC and not _is_intent(a, _ALL_INTENT)}
    from app.rag.districts import find_districts
    for p in programs:
        # a city in the name ("... কোর্স (ব্যাচ-৪৯৯ZJ) যশোর") is not what the program is; a city the guest asks for is
        # checked below (extras), so it never makes the batch in that city look less like "the course"
        words = ([w for w in content_words(p.name) if w not in NEUTRAL_WORDS and not find_districts(w)]
                 or content_words(p.name))
        hit = [w for w in words if _matches(w, asked)]
        # a distinctive word of the name ("yoga") is enough; a generic one ("course") only when it is much of the name.
        # Distinctive means it matches something specific the guest said, not their "প্রোগ্রাম"/"course"/"next"
        # ("প্রো-মাস্টার" must not look named by "প্রোগ্রাম কবে?")
        distinctive = [w for w in hit if w not in _GENERIC and _matches(w, specific)]
        if hit and (distinctive or len(hit) / len(words) >= 0.5):
            named.append(p)
    candidates = named or (programs if generic_word else [])
    if not candidates:
        return None
    # words that change the subject and that no candidate knows ("yoga" vs a "Quantum Method Course" row)
    extras = [w for w in dict.fromkeys(content_words(text)) if w not in NEUTRAL_WORDS and w not in _FILLER
              and w not in _PROGRAM_ALIASES and not _is_intent(w, _ALL_INTENT)]
    if extras:
        def knows(p: Program) -> bool:
            known = set(content_words(f"{p.name} {p.place or ''} {p.notes or ''}"))
            return all(_matches(w, known) for w in extras)
        candidates = [p for p in candidates if knows(p)]
        if not candidates:
            return None
    # A next-program request needs one precise answer, not a list.
    if any(_canon(t) in {'next', 'porer', 'পরবর্তী', 'পরের'} for t in tokens):
        candidates = candidates[:1]
    asked_any = wants_date or wants_fee or wants_place
    # "kobe / kothay" not said: date and place; the fee only when asked (a free program says so anyway, _sentence)
    return ProgramQuestion(candidates[:MAX_PROGRAMS], wants_date or not asked_any, wants_fee,
                           wants_place or not asked_any, generic=not named)


_FREE = {"free", "0", "0 tk", "0 taka", "ফ্রি", "বিনামূল্যে", "বিনা মূল্যে", "ফি নেই", "০", "০ টাকা", "free of cost"}


def is_free(p: Program) -> bool:
    return (p.fee or "").strip().strip(".।").lower() in _FREE


def quoted(name: str) -> str:
    return f'"{name}"'


def _sentence(p: Program, q: ProgramQuestion, today: date, lang: str) -> str:
    """One program, from the table only: "<name>" অনুষ্ঠিত হবে <dates>, <place>। ফি: <fee>।"""
    bn = lang == "bn"
    parts = []
    if q.wants_date:
        when = format_dates(p.start_date, p.end_date, lang) + f" ({days_left_text(p, today, lang)})"
        if p.place:  # "<name>" অনুষ্ঠিত হবে ২০–২৩ নভেম্বর ২০২৬, ঢাকা।
            when += f", {p.place}"
        parts.append((f"অনুষ্ঠিত হবে {when}" if bn else f"takes place on {when}"))
    elif q.wants_place:
        parts.append((f"হবে {p.place}-এ" if bn else f"takes place at {p.place}") if p.place
                     else ("স্থান এখনো জানানো হয়নি" if bn else "venue not announced yet"))
    free = is_free(p)
    if q.wants_fee or free:  # the fee only when asked; "free" always, it invites the guest
        fee = p.fee or ("এখনো জানানো হয়নি" if bn else "not announced yet")
        fee_part = ("ফি: " if bn else "fee: ") + fee
        if free:  # a free program says so first
            parts.insert(0, fee_part)
        else:
            parts.append(fee_part)
    if q.wants_date and q.wants_place and not p.place:
        parts.append("স্থান এখনো জানানো হয়নি" if bn else "venue not announced yet")
    sep = "। " if bn else ". "
    head, *rest = parts or [""]
    text = f"{quoted(p.name)} {head}" if head and not head.startswith(("ফি", "fee")) else f"{quoted(p.name)} — {head}"
    return text + "".join(sep + r for r in rest) + ("।" if bn else ".")


def compose_answer(q: ProgramQuestion, text: str, today: date | None = None) -> str:
    """Short answer straight from the table (used when no AI model is available). Program names always in quotes."""
    today = today or today_bd()
    lang = "en" if detect(text) == "en" else "bn"
    lines = [_sentence(p, q, today, lang) for p in q.programs]
    if len(lines) == 1:
        return lines[0]
    head = "আসন্ন প্রোগ্রাম (নিকটতম আগে):" if lang == "bn" else "Upcoming programs (nearest first):"
    return head + "\n" + "\n".join(f"• {l}" for l in lines)


def quote_program_names(text: str, programs: list[Program]) -> str:
    """An AI-written program answer: every program name it mentions goes inside quotation marks."""
    for p in sorted(programs, key=lambda x: len(x.name), reverse=True):
        name = p.name.strip()
        if not name:
            continue
        out, i = [], 0
        while (j := text.find(name, i)) >= 0:
            before, after = text[j - 1:j], text[j + len(name):j + len(name) + 1]
            out.append(text[i:j])
            out.append(name if before in ('"', "“", "'") and after in ('"', "”", "'") else quoted(name))
            i = j + len(name)
        out.append(text[i:])
        text = "".join(out)
    return text


def programs_in_district(programs: list[Program], district: str) -> list[Program]:
    """Upcoming programs whose place names exactly this district (no neighbouring districts)."""
    from app.rag.districts import find_districts
    return [p for p in programs if district in find_districts(f"{p.place or ''} {p.name}")]
