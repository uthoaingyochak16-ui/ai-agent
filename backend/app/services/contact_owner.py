"""Whose phone number / email is this? The guest's own, a relative's, or someone else's (a friend, a doctor, a shop,
our own office)?

Only the guest's own contact goes on their profile (and may link it to another profile), so this is decided before
anything is saved:

1. business  - the number/email appears in our own knowledge base, Q&A, variables or programs ("01714-..., apnader
               office e call dilam"): never the guest's.
2. family    - a relative's possessive right before it ("amar bhai er number 017...", "আম্মুর নম্বর"): kept on that
               relative (family.upsert), not on the guest.
3. other     - another person or an organisation ("office er number", "doctor er", "bondhur", "apnader"): not kept.
4. self      - "amar number", "my number", "call me", "আমাকে কল দিন".
5. unknown   - nothing around it. It is the guest's when we just asked for it (lead capture) or the message is
               little more than the number; otherwise the AI decides from the context (post_reply.collect_guest_info).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Program, QAPair, Variable
from app.privacy.patterns import EMAIL_RE, PHONE_CAND_RE, ascii_digits
from app.services.contacts import extract_emails, extract_phones, normalize_phone
from app.services.settings_service import get_setting

_S = r"(?<![a-zA-Zঀ-৿])"          # word start (works with Bangla)
_E = r"(?=[\s,.?!।:;\-'’]|$)"               # word end

# a relative's possessive: "bhai er", "bhaier", "ammur", "মায়ের", "my mother's"
_REL = (r"(ammu|amma|abbu|abba|baba|ma|maa|bhai|bhaiya|vai|bon|apu|chele|meye|bou|wife|husband|shami|stri|nana|nani|"
        r"dada|dadi|mama|mami|chacha|chachi|khala|khalu|fupi|fufu|son|daughter|mother|father|brother|sister|uncle|aunt|"
        r"cousin|মা|আম্মু|আম্মা|আব্বু|আব্বা|বাবা|ভাই|ভাইয়া|বোন|আপু|ছেলে|মেয়ে|মেয়ে|স্ত্রী|স্বামী|বউ|নানা|নানী|দাদা|দাদী|মামা|খালা|চাচা)")
_FAMILY = re.compile(_S + _REL + r"(\s*(er|r|ar|য়ের|এর|ের|র|'s|s))" + _E + "|" + _S +
                     r"(ammur|abbur|mayer|babar|bhaier|bhaiyer|boner|cheler|meyer|bour|shamir|strir|mamar|khalar|chachar|"
                     r"মায়ের|মার|বাবার|আম্মুর|আব্বুর|ভাইয়ের|বোনের|ছেলের|মেয়ের|স্ত্রীর|স্বামীর|মামার|খালার|চাচার)" + _E, re.IGNORECASE)
_OTHER = re.compile(_S + r"(office|অফিস|company|কোম্পানি|shop|dokan|দোকান|doctor|daktar|ডাক্তার|hospital|হাসপাতাল|clinic|"
                    r"ক্লিনিক|branch|শাখা|center|centre|সেন্টার|কেন্দ্র|quantum|কোয়ান্টাম|apnader|আপনাদের|tomader|তোমাদের|your|"
                    r"helpline|hotline|হেল্পলাইন|agent|manager|ম্যানেজার|sir|স্যার|madam|ম্যাডাম|bondhu|bondhur|বন্ধু|বন্ধুর|"
                    r"friend|friend's|neighbou?r|protibeshi|প্রতিবেশী|colleague|boss|teacher|shikkhok|শিক্ষক|school|স্কুল|"
                    r"college|কলেজ|bank|ব্যাংক|ngo|organization|organisation|প্রতিষ্ঠান|protishthan|someone|kono ekjon|"
                    r"his|her|their|onar|তার|ওনার|উনার)" + _E, re.IGNORECASE)
_SELF = re.compile(_S + r"(amar|amr|my|mine|আমার|nijer|নিজের|amake|amk|আমাকে|me|personal|ব্যক্তিগত)" + _E, re.IGNORECASE)
_PIECE_END = re.compile(r"[\n.?!।;,]")


_RELATION_EN = (  # (word starts, relation) checked in order: "mama" before "ma", "bhaier" -> brother
    (("mama", "মামা", "chacha", "চাচা", "khalu", "uncle"), "uncle"),
    (("mami", "khala", "খালা", "chachi", "fupi", "fufu", "aunt"), "aunt"),
    (("nana", "নানা", "dada", "দাদা", "grandfather"), "grandfather"),
    (("nani", "নানী", "dadi", "দাদী", "grandmother"), "grandmother"),
    (("ammu", "amma", "ma", "মা", "আম্মু", "আম্মা", "mother"), "mother"),
    (("abbu", "abba", "baba", "বাবা", "আব্বু", "আব্বা", "father"), "father"),
    (("bhai", "vai", "ভাই", "brother"), "brother"),
    (("bon", "apu", "বোন", "আপু", "sister"), "sister"),
    (("chele", "ছেলে", "son"), "son"),
    (("meye", "মেয়ে", "মেয়ে", "daughter"), "daughter"),
    (("bou", "wife", "stri", "স্ত্রী", "বউ"), "wife"),
    (("shami", "husband", "স্বামী"), "husband"),
)


def relation_en(word: str | None) -> str | None:
    """"bhai er" / "ammur" / "স্বামীর" -> brother / mother / husband."""
    w = (word or "").strip().lower()
    if w.startswith(("bondhu", "bondu", "bonus")):  # a friend, not "bon" (sister)
        return None
    for starts, rel in _RELATION_EN:
        if w.startswith(starts):
            return rel
    return None


@dataclass(frozen=True)
class Owner:
    kind: str                   # self | family | other | business | unknown
    relation: str | None = None  # for family: the word that named the relative ("bhai", "ammur")


def _around(text: str, start: int, end: int) -> tuple[str, str]:
    """(the words right before the number, back to the previous comma/full stop; a few words right after it)."""
    before = text[:start]
    cut = max((m.end() for m in _PIECE_END.finditer(before)), default=0)
    after = text[end:end + 60]
    stop = _PIECE_END.search(after)
    return before[cut:][-60:], (after[:stop.start()] if stop else after)


_OWN_NUMBER = re.compile(r"(?i)" + _S + r"(amar|amr|my|আমার|nijer|নিজের)\s+(personal\s+|ব্যক্তিগত\s+)?(phone\s+|mobile\s+|whatsapp\s+|"
                         r"ফোন\s+|মোবাইল\s+)?(number|nombor|nambar|no|phone|mobile|cell|email|mail|নম্বর|নাম্বার|ফোন|মোবাইল|ইমেইল)"
                         r"[\s:.\-–]*(holo|hocche|is|হলো|হচ্ছে)?[\s:.\-–]*$")
_CALL_ME = re.compile(r"(?i)call\s+(me|dio|diben|den)|amake\s+(call|phone|kol)|আমাকে\s+(কল|ফোন)|" + _S + r"(amake|আমাকে|me)" + _E)
_REL_WORD = re.compile(_S + _REL + r"\w*" + _E + "|" + _FAMILY.pattern, re.IGNORECASE)


def _read(ctx: str, *, before: bool) -> Owner | None:
    if before and _OWN_NUMBER.search(ctx):  # "amar number 017..." right before it: theirs, whatever came earlier
        return Owner("self")
    rel = next((m.group(0).strip() for m in _REL_WORD.finditer(ctx) if relation_en(m.group(0).strip())), None)
    if rel:                                  # "amar bhai Rakib er number 017...": the brother's
        return Owner("family", rel)
    if _OTHER.search(ctx):
        return Owner("other")
    if _SELF.search(ctx) or _CALL_ME.search(ctx):
        return Owner("self")
    return None


def classify(text: str, start: int, end: int, *, business: bool = False) -> Owner:
    """What comes before the number decides ("amar bhai er number 017..."); only when that says nothing, the words
    after it ("017... eta amar number")."""
    if business:
        return Owner("business")
    before, after = _around(text, start, end)
    return _read(before, before=True) or _read(after, before=False) or Owner("unknown")


def mostly_contact(text: str) -> bool:
    """"01712345678" / "Rahim, 01712345678, Mirpur": nothing else in the message talks about other people."""
    rest = EMAIL_RE.sub(" ", PHONE_CAND_RE.sub(" ", ascii_digits(text or "")))
    return len(re.findall(r"\w+", rest)) <= 8 and not _OTHER.search(rest) and not _FAMILY.search(rest)


# ---- our own numbers and emails ------------------------------------------------------------------------------------
_cache: dict = {}


async def business_contacts(session: AsyncSession) -> tuple[set[str], set[str]]:
    """Every phone (E.164) and email written in our knowledge base, Q&A, variables and programs. Cached until the
    knowledge changes (context_version)."""
    version = await get_setting(session, "context_version") or 1
    if _cache.get("version") == version:
        return _cache["phones"], _cache["emails"]
    texts: list[str] = []
    for col in (Chunk.content, QAPair.answer_template, Variable.value, Program.place, Program.name):
        texts += [t for t in (await session.execute(select(col))).scalars() if t]
    phones: set[str] = set()
    emails: set[str] = set()
    for t in texts:
        phones.update(extract_phones(t))
        emails.update(extract_emails(t))
    _cache.update(version=version, phones=phones, emails=emails)
    return phones, emails


async def public_contacts(session: AsyncSession) -> frozenset[str]:
    """Our own numbers and emails: public, so stored messages keep them readable (privacy.redaction keep=)."""
    phones, emails = await business_contacts(session)
    return frozenset(phones | emails)


async def owners(session: AsyncSession, text: str) -> list[tuple[str, str, Owner]]:
    """[(kind "phone"|"email", normalized value, owner)] for every contact in the message, in order."""
    biz_phones, biz_emails = await business_contacts(session)
    norm = ascii_digits(text or "")
    out: list[tuple[str, str, Owner]] = []
    for m in PHONE_CAND_RE.finditer(norm):
        value = normalize_phone(m.group())
        if value and all(v != value for _, v, _ in out):
            out.append(("phone", value, classify(norm, m.start(), m.end(), business=value in biz_phones)))
    for m in EMAIL_RE.finditer(norm):
        value = m.group().lower()
        if all(v != value for _, v, _ in out):
            out.append(("email", value, classify(norm, m.start(), m.end(), business=value in biz_emails)))
    return out
