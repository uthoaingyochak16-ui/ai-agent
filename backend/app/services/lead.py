"""Guest details: full name and phone number, asked ONCE per guest (one guest = one profile, on every channel).

- First meaningful message of a guest whose profile lacks them (a bare "hi" / small talk does not count): say why
  ("to stay in touch and help when needed"), ask, and hold the question; it is answered right after the reply,
  whatever the reply is. Details already on the profile (from any earlier chat or channel) are never asked for.
- The reply: phone by pattern (identity.handle_shared_contacts already saw it), name (and a location, if they add
  one) by the "extraction" route, or a comma split when no route is set. Sharing details after being told why counts
  as consent to save them.
- Profile.personal_info_request_status: not_asked -> asked -> provided | declined | deferred (ignored). After
  declined / deferred nothing is asked again; only an action that truly needs a phone (callback, enrolment) asks,
  once, optionally (services/tools.py). A confused "keno?" gets the reason once (Profile.lead_asks 2).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMUnavailable
from app.models import ProfileFact
from app.privacy.guard import PrivacyGuard
from app.privacy.patterns import PHONE_CAND_RE, ascii_digits
from app.privacy.redaction import redact_for_storage
from app.services import consent as consent_svc
from app.services.contacts import extract_phones
from app.services.greeting import is_greeting
from app.services.language import detect, localized
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting
from app.services.smalltalk import smalltalk_reply

FIELDS = ("name", "phone")  # what is asked for; a location the guest adds is saved too
_LABELS = {"bn": {"name": "নাম", "phone": "ফোন নম্বর", "location": "ঠিকানা"},
           "en": {"name": "name", "phone": "phone number", "location": "location"}}
_EXAMPLES = {"bn": {"name": "রহিম উদ্দিন", "phone": "01XXXXXXXXX", "location": "মিরপুর, ঢাকা"},
             "en": {"name": "Rahim Uddin", "phone": "01XXXXXXXXX", "location": "Mirpur, Dhaka"}}

EXTRACT_SYSTEM = """The assistant asked the guest for their name, phone number and location (city/area/address).
From the guest's reply, return ONLY a JSON object, no prose:
{"name": string|null, "location": string|null, "reply": "details"|"confused"|"refused"|"other"}
Rules: only what the guest wrote about THEMSELVES; null when not given; keep the guest's spelling and script;
the name without words like "amar nam"/"আমার নাম"/"my name is"; location as written (e.g. "Mirpur, Dhaka").
Never the name or address of someone else (a relative, friend, our office or branch, a company): "amar bhai Karim" is
not their name, "Mirpur branch e jabo" is not where they live.
"reply": details = they gave some of the details; confused = they ask why it is needed, what to write, or did not
understand ("keno?", "ki dibo?", "bujhi nai", "কেন লাগবে?"); refused = they will not give it; other = anything else
(e.g. a new question)."""

# "are you saving my data?" / "আমার তথ্য কি সংরক্ষণ করছেন?" / "keno amar number lagbe?"
_DATA = r"(data|info|information|details|tottho|totho|তথ্য|ডাটা|ডেটা|number|nambar|নম্বর|নাম্বার)"
_ASKS_ABOUT_DATA = [
    re.compile(r"(?i)(sav(e|ing|ed)|stor(e|ing|ed)|keep(ing)?|record|rakh\w*|songrokkhon\w*|sangrakkhan\w*|সংরক্ষণ|সেভ|রাখ\w*|জমা)"
               r"[^.?!]{0,40}" + _DATA + r"|" + _DATA + r"[^.?!]{0,40}(sav(e|ing|ed)|stor(e|ing|ed)|keep(ing)?|rakh\w*|"
               r"songrokkhon\w*|সংরক্ষণ|সেভ|রাখ\w*|জমা)"),
    re.compile(r"(?i)(keno|kno|why|কেন)[^.?!]{0,40}" + _DATA + r"|" + _DATA + r"[^.?!]{0,40}(keno|kno|why|কেন)"),
]
_QUESTION = re.compile(r"(?i)\?|\b(ki|kি|keno|kno|why|do|are|is|will)\b|কি|কী|কেন|নাকি")

# ---- reading "amar name Rahim, phone 017..., Mirpur" without the AI (fallback, and to fill a field the AI missed) ----
_END = r"(?=[\s:.\-–]|$)"  # Python's \b does not work after Bangla vowel signs
_SEP = re.compile(r"[,;\n।|/]+|(?<!Md)(?<!Mr)(?<!Dr)(?<!Mst)(?<!Mrs)\.\s+|\s+(?:and|ebong|এবং|kintu|but|r|ar|আর)\s+(?=\S)",
                  re.IGNORECASE)
_LEAD_CONJ = re.compile(r"(?i)^(and|ebong|এবং|kintu|but|r|ar|আর|o|ও)\s+")
_PHONE_ONLY = re.compile(r"(?i)^((eta|eita|this\s+is|its|it's|amar|my|আমার|এটা|এটি|call|me|on|at|my|is|holo|হলো|"
                         r"phone|fon|mobile|mob|cell|number|nambar|no|contact|ফোন|মোবাইল|নম্বর|নাম্বার|যোগাযোগ)[\s:.\-–]*)+$")
_RELATIVE_NAME = re.compile(r"(?i)(?<![a-zঀ-৿])(ammur|ammu|abbur|abbu|mayer|mar|babar|bhaier|bhaiyer|boner|cheler|meyer|bour|"
                            r"shamir|mother'?s?|father'?s?|wife'?s?|husband'?s?|son'?s?|daughter'?s?|"
                            r"মায়ের|মার|বাবার|আম্মুর|আব্বুর|ভাইয়ের|বোনের|ছেলের|মেয়ের|স্ত্রীর|স্বামীর)\s+(nam|name|নাম)" + _END)
_NAME_LABEL = re.compile(r"(?i)^(?:amar\s+|আমার\s+|my\s+)?(?:full\s+)?(?:name|nam|নাম)" + _END + r"\s*(?:is|holo|hocche|হলো|হচ্ছে)?"
                         r"[\s:.\-–]*(?P<v>.+)$|^(?:i\s+am|i'm|ami|আমি|this\s+is)\s+(?P<w>.+)$")
_LOC_LABEL = re.compile(r"(?i)^(?:amar\s+|আমার\s+|my\s+)?(?:address|adress|thikana|ঠিকানা|location|লোকেশন|basa|বাসা|"
                        r"area|এলাকা|district|জেলা|city|i\s+live\s+in|live\s+in|from|thaki|থাকি)" + _END +
                        r"[\s:.\-–]*(?:is|holo|হলো)?[\s:.\-–]*(?P<v>.+)$")
_LOC_SUFFIX = re.compile(r"(?i)^(?:ami\s+|আমি\s+)?(?P<v>.+?)\s*(?:-?e|-?te|এ|তে)?\s+(?:thaki|থাকি|theke|থেকে|basa|বাসা|e\s+thaki)\s*$")
_TRAIL = re.compile(r"(?i)\s+(e|te|এ|তে)$")


_STRONG = re.compile(r"(?i)^(?:amar\s+|আমার\s+|my\s+)?(?:full\s+)?(?:name|nam|নাম|address|adress|thikana|ঠিকানা|location|লোকেশন|basa|বাসা)" + _END)


def _labeled_parts(text: str, strict: bool = False) -> tuple[dict, list[str], bool]:
    """-> ({"name"/"location": value written after a label}, unlabeled pieces in order, a phone was there).
    strict: only explicit labels ("name: X", "ঠিকানা X"), not "ami X" / "X e thaki"."""
    norm = ascii_digits(text or "")
    had_phone = bool(extract_phones(norm))
    t = PHONE_CAND_RE.sub("\n", norm)
    found: dict = {}
    loose: list[str] = []
    last = None
    for seg in _SEP.split(t):
        seg = _LEAD_CONJ.sub("", re.sub(r"\s+", " ", seg or "").strip(" .:-–"))
        if not seg or not re.search(r"\w", seg) or _PHONE_ONLY.match(seg) or _RELATIVE_NAME.search(seg):
            last = None if seg and _RELATIVE_NAME.search(seg) else last
            continue
        loc = _LOC_LABEL.match(seg) or _LOC_SUFFIX.match(seg)
        name = None if loc else _NAME_LABEL.match(seg)
        if strict and not _STRONG.match(seg):
            loc = name = None
        if loc and "location" not in found:
            found["location"], last = _TRAIL.sub("", loc.group("v").strip(" .:-–")), "location"
        elif name and "name" not in found:
            found["name"], last = (name.group("v") or name.group("w")).strip(" .:-–"), "name"
        elif last == "location" and len(seg.split()) <= 4 and not any(ch.isdigit() for ch in seg):
            found["location"] += f", {seg}"  # "ঠিকানা: আগ্রাবাদ, চট্টগ্রাম"
        else:
            loose.append(seg)
            last = None
    found = {k: v for k, v in found.items() if v and len(v.split()) <= (5 if k == "name" else 10)}
    if "name" in found and any(ch.isdigit() for ch in found["name"]):
        del found["name"]
    return found, loose, had_phone


def _join(labels: list[str], lang: str) -> str:
    if len(labels) == 1:
        return labels[0]
    sep = " ও " if lang == "bn" else " and "
    return ", ".join(labels[:-1]) + sep + labels[-1]


async def has_phone(session: AsyncSession, p) -> bool:
    return "phone" not in await missing(session, p)


async def missing(session: AsyncSession, p) -> list[str]:
    """Fields to ask for that are not on the profile yet (a value held for consent counts as given: never ask for it twice).
    A phone that belongs to another profile is kept there (staff get a merge suggestion) but was given all the same."""
    pending = set((await session.execute(select(ProfileFact.key).where(
        ProfileFact.profile_id == p.id, ProfileFact.key.in_([*(f"pending_{f}" for f in FIELDS), "merge_idx_phone"])))).scalars())
    have = {"name": bool(p.name), "phone": bool(p.phone_hash) or "merge_idx_phone" in pending}
    return [f for f in FIELDS if not have[f] and f"pending_{f}" not in pending]


async def _text(session: AsyncSession, key: str, fields: list[str], text: str, lang: str | None = None) -> str:
    lang = "en" if (lang or detect(text)) == "en" and await get_setting(session, f"{key}_en") else "bn"
    tpl = await localized(session, key, text, lang)
    example = ", ".join(_EXAMPLES[lang][f] for f in fields)
    return tpl.replace("{fields}", _join([_LABELS[lang][f] for f in fields], lang)).replace("{example}", example)


def asks_about_data(text: str) -> bool:
    """The guest asks whether / why we keep their details."""
    t = text or ""
    return bool(_QUESTION.search(t)) and any(rx.search(t) for rx in _ASKS_ABOUT_DATA)


async def data_answer(session: AsyncSession, r, text: str) -> str:
    """Yes, and why; then, only while our one request is still open, the request with an example."""
    reply = await localized(session, "data_saving_reply", text)
    p = r.profile
    if await get_setting(session, "lead_capture_enabled") and not p.erased_at \
            and p.personal_info_request_status in ("not_asked", "asked") and (still := await missing(session, p)):
        reply = f"{reply}\n\n{await _text(session, 'lead_explain', still, text)}"
        p.lead_asks = max(p.lead_asks, 1)
        p.personal_info_request_status = "asked"
        r.conversation.lead_awaiting_at = datetime.now(timezone.utc)
    return reply


def _fallback_split(text: str) -> dict:
    """No AI route. Labels first ("name X", "নাম: X", "ঠিকানা: X", "X e thaki", "ami X"); then unlabeled pieces in
    order ("Rahim, 01712345678, Mirpur Dhaka" -> name, location)."""
    found, loose, had_phone = _labeled_parts(text)
    strong = _labeled_parts(text, strict=True)[0]
    if not (strong or had_phone) and len(found) + len(loose) < 2:  # "na thak" / "ami jante chai" is not a name
        return {}
    loose = [x for x in loose if len(x.split()) <= 6 and not any(ch.isdigit() for ch in x)]
    for k in ("name", "location"):
        if k not in found and loose:
            found[k] = loose.pop(0)
    if loose and "location" in found and found["location"] not in loose:
        found["location"] += ", " + ", ".join(loose[:2])  # "Mirpur, Dhaka" split by the comma
    return {"name": found.get("name"), "location": found.get("location")}


async def _extract(session: AsyncSession, llm, r, text: str) -> dict:
    def build(policy):
        g = PrivacyGuard(policy)
        return g.prepare_task(EXTRACT_SYSTEM, text)
    try:
        out = await llm.run(session, "extraction", build, channel=r.identity.channel, conversation_id=r.conversation.id,
                            is_test=r.identity.external_id.startswith("test:"))
    except LLMUnavailable:
        return _fallback_split(text)
    data = parse_json_object(out.result.text) or {}
    restore = out.request.redactor.restore  # a location may have been sent as [ADDRESS_1]
    out_ = {k: restore(str(data[k])).strip() or None if data.get(k) else None for k in ("name", "location")}
    out_["reply"] = data.get("reply") if data.get("reply") in ("details", "confused", "refused", "other") else None
    labeled, _, _ = _labeled_parts(text, strict=True)  # "নাম: X" the AI somehow missed: take the clearly labelled value
    for k in ("name", "location"):
        if not out_[k] and labeled.get(k):
            out_[k] = labeled[k]
    return out_


async def capture(session: AsyncSession, llm, r, text: str) -> tuple[list[str], str | None]:
    """Save the lead details in the guest's reply. Returns (fields saved, kind of reply: details | confused |
    refused | other | None when unknown)."""
    p = r.profile
    if not text.strip():
        return [], None
    phone = bool(extract_phones(text))
    values = await _extract(session, llm, r, text)
    kind = values.get("reply")
    if asks_about_data(text) or re.fullmatch(r"(?i)\s*(keno|kno|why|কেন|ki|কি|কী)\s*[?।.!]*\s*", text):
        kind = "confused"
    if not (phone or values.get("name") or values.get("location")):
        return [], kind
    if not p.consent_given:  # they shared it after being told why: that is the consent
        await consent_svc.record_consent(session, p, True)  # also saves the phone held by handle_shared_contacts
    saved = ["phone"] if phone and "phone" not in await missing(session, p) else []
    for k in ("name", "location"):
        if values.get(k) and await consent_svc.apply_field(session, p, k, values[k], consent_checked=True) == "saved":
            saved.append(k)
    await session.flush()
    return saved, "details"


async def before_answer(session: AsyncSession, llm, r, question: str) -> tuple[str | None, str]:
    """Runs before every AI answer. Returns (text to send INSTEAD of answering, question to answer).
    May set r.reply_prefix / r.reply_suffix, which the next reply carries."""
    if not await get_setting(session, "lead_capture_enabled"):
        return None, question
    return await _before_answer(session, llm, r, question)


async def _before_answer(session: AsyncSession, llm, r, question: str) -> tuple[str | None, str]:
    p, conv = r.profile, r.conversation

    if conv.lead_awaiting_at is not None:  # we asked last turn: this message may hold the details
        conv.lead_awaiting_at = None
        held, conv.lead_pending_question = conv.lead_pending_question, None
        saved, kind = await capture(session, llm, r, question)
        r.events.append(f"lead details saved: {', '.join(saved)}" if saved else f"lead details not given ({kind or 'unknown'})")
        still = await missing(session, p)
        said = held or question  # "Rahim, 017..., Dhaka" has no language of its own: follow the guest's question
        if not saved and still and kind == "confused" and p.lead_asks < 2:  # "keno?": say why once, keep waiting
            p.lead_asks = 2
            conv.lead_awaiting_at, conv.lead_pending_question = datetime.now(timezone.utc), held
            text = await data_answer(session, r, question) if asks_about_data(question) else \
                await _text(session, "lead_explain", still, said)
            return text, question
        if saved:
            p.personal_info_request_status = "provided"
            if not still:  # all details in: thank them and ask whether they need anything else
                r.events.append("lead complete")
                if not held:
                    return await localized(session, "lead_complete", said), question
                r.reply_prefix = await localized(session, "lead_thanks", said)
                r.reply_suffix = await localized(session, "lead_anything_else", said)
                return None, held
            thanks = await localized(session, "lead_thanks", said)
            if not held:  # the message was only the details: nothing else to answer
                return thanks, question
            r.reply_prefix = thanks
            return None, held
        # not given: never asked again in this or any later conversation (only a real action may ask, once)
        p.personal_info_request_status = "declined" if kind == "refused" else "deferred"
        r.events.append(f"personal info {p.personal_info_request_status}")
        if kind == "refused":
            ack = await localized(session, "lead_declined_ack", said)
            if held:
                r.reply_prefix = ack
                return None, held
            return ack, question
        return None, "\n".join(x for x in (held, question) if x)  # ignored the request: answer what they asked

    if p.personal_info_request_status != "not_asked" or p.lead_asks > 0 or p.erased_at:
        return None, question
    still = await missing(session, p)
    if not still:  # one guest = one profile: details from an earlier chat / another channel count
        p.personal_info_request_status = "provided"
        return None, question
    if is_greeting(question, list(await get_setting(session, "greeting_words") or [])) \
            or await smalltalk_reply(session, question):
        return None, question  # "hi" / "thanks" is not a meaningful conversation yet: ask with the first real one
    p.lead_asks = 1
    p.personal_info_request_status = "asked"
    conv.lead_awaiting_at = datetime.now(timezone.utc)
    r.events.append(f"lead details asked: {', '.join(still)}")
    ask = await _text(session, "lead_prompt", still, question)
    conv.lead_pending_question = redact_for_storage(question)[:2000]
    return ask, question
