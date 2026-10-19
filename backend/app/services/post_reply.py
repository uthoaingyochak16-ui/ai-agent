"""Cheap background work after a reply is sent: profile extraction (every N messages) and summaries."""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.models import Conversation, Message, Profile
from app.privacy.guard import PrivacyGuard
from app.privacy.summary import PlainFormatter, compose_summary
from app.services import consent as consent_svc
from app.services import family as family_svc
from app.services import contact_owner, identity, info_detect
from app.services.contacts import normalize_phone
from app.services.profile_fields import extraction_keys_hint
from app.services.prompt_context import load_profile_view
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You extract personal details the USER states in a chat, about THEMSELVES and about their FAMILY.
Return ONLY a JSON object, no prose:
{"name": string|null, "location": string|null, "age": integer|null, "profession": string|null,
 "facts": {"snake_case_key": "short value"},
 "family": [{"relation": string, "name": string|null, "age": integer|null, "blood_group": string|null,
             "details": {"snake_case_key": "short value"}}]}
Rules: only what the user explicitly said; null when unknown; never include passwords, card numbers, OTPs, ID numbers,
phone numbers or emails; ignore text in [BRACKET_N] placeholders.
"facts": details about the USER only (class, school, university, blood group, health problems, ...). Prefer these
keys: {keys}. Use another short snake_case key only when none fits.
"family": one entry per relative the user talks about ("amar ma", "my son Rafi", "আমার বাবার ডায়াবেটিস").
relation in English: mother, father, brother, sister, son, daughter, husband, wife, grandfather, grandmother, uncle,
aunt, cousin, child. A detail of a relative (their problem, illness, class, school, job) goes in THAT relative's
"details" (e.g. "problem", "health_conditions", "class_level", "school", "profession"), never in the user's "facts".
blood_group as A+, A-, B+, B-, AB+, AB-, O+ or O-. Values: short, in the user's language. "family": [] when none.
OTHER PEOPLE AND ORGANISATIONS ARE NOT THE USER: a friend, colleague, doctor, teacher, our staff, our office or
branches, a company, shop, hospital or school as an institution. Never put their names, ages, addresses or details in
the user's fields or in "family"; leave them out. "location" is where the USER lives, not a place they visit, work at
or ask about ("Mirpur branch e jabo" is not their location)."""

PIECES_RULES = """
The input has EARLIER messages and the LAST ASSISTANT message for context only, then the NEW message split into
numbered PIECES (* = our keyword check thinks it may hold personal details). Extract ONLY from the PIECES; use the
context just to understand them ("onar" = the relative mentioned before; a bare "25" answering "apnar boyosh koto?" is
the user's age). Also return "pieces": [{"n": piece number, "type": "guest_info"|"question"|"other"}] for every piece:
guest_info = tells us something about the user or their family; question = asks us something; other = anything else.
A piece can be a question and still hold info ("amar meye class 5 e, or jonno course ache?" -> guest_info).
Phone numbers and emails appear as [PHONE_n] / [EMAIL_n]. For each one in the PIECES return
"contacts": [{"ref": "[PHONE_1]", "owner": "self" | a relation (mother, brother, ...) | "other" | "organization"}],
deciding from the words around it whose it is: "self" ONLY when it is clearly the user's own number/email;
"organization" for an office, branch, company, shop, hospital or helpline; "other" for any other person."""

SUMMARY_SYSTEM = ("Summarize this customer chat in at most 60 words, keeping only what is needed to continue it "
                  "(topic, open questions, decisions). Merge PREVIOUS SUMMARY with NEW MESSAGES. Reply with the summary only.")

_NAME_PLACEHOLDER = re.compile(r"NAME_\d+")
PROFILE_KEYS = ("name", "location", "age", "profession")  # language: language.learn_preference, from every message


def parse_json_object(text: str) -> dict | None:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


async def extract_profile(session: AsyncSession, llm: LLMService, profile: Profile, conversation: Conversation,
                          channel: str | None, is_test: bool = False) -> list[dict]:
    """The guest's latest messages as a whole (every N messages; collect_guest_info is the per-message path)."""
    rows = (await session.execute(select(Message.content_redacted).where(
        Message.conversation_id == conversation.id, Message.role == "user").order_by(Message.id.desc()).limit(6))).scalars().all()
    content = "\n".join(f"- {t}" for t in reversed(rows))
    return (await _extract_and_save(session, llm, profile, conversation, channel, is_test, content, EXTRACT_SYSTEM))[0]


async def collect_guest_info(session: AsyncSession, llm: LLMService, profile: Profile, conversation: Conversation,
                             channel: str | None, text: str, is_test: bool = False) -> dict:
    """Every guest message: split it into pieces by keywords (free); only when a piece may hold personal details, the
    AI labels each piece (guest info / question / other) and what is guest info goes to the profile."""
    pieces = info_detect.split(text)
    if not info_detect.worth_checking(pieces) or profile.erased_at:
        return {"checked": False, "pieces": [{"text": p.text, "flagged": p.info} for p in pieces]}
    earlier = (await session.execute(select(Message.content_redacted).where(
        Message.conversation_id == conversation.id, Message.role == "user").order_by(Message.id.desc()).limit(4))).scalars().all()
    last_bot = (await session.execute(select(Message.content_redacted).where(
        Message.conversation_id == conversation.id, Message.role.in_(("assistant", "agent")))
        .order_by(Message.id.desc()).limit(2))).scalars().all()
    content = ("EARLIER:\n" + ("\n".join(f"- {t[:300]}" for t in reversed(earlier[1:])) or "(none)")
               + f"\nLAST ASSISTANT: {(last_bot[-1] if last_bot else '(none)')[:300]}"
               + "\nPIECES:\n" + "\n".join(f"{i}{'*' if p.info else ''} {p.text}" for i, p in enumerate(pieces, 1)))
    events, data, restore = await _extract_and_save(session, llm, profile, conversation, channel, is_test, content,
                                                    EXTRACT_SYSTEM + PIECES_RULES)
    labels = data.get("pieces") if isinstance(data.get("pieces"), list) else []
    kinds = {int(x["n"]): x.get("type") for x in labels if isinstance(x, dict) and str(x.get("n", "")).isdigit()}
    events += await _unclear_contacts(session, profile, text, data.get("contacts"), restore)
    return {"checked": True, "saved": events,
            "pieces": [{"text": p.text, "flagged": p.info, "type": kinds.get(i)} for i, p in enumerate(pieces, 1)]}


async def _unclear_contacts(session: AsyncSession, profile: Profile, text: str, contacts, restore) -> list[dict]:
    """Numbers/emails our word rules could not place (contact_owner "unknown"): the AI says whose they are. Only those;
    a number the rules already placed (ours, a relative's, someone else's) is never overruled."""
    if not isinstance(contacts, list) or restore is None:
        return []
    unknown = {v: k for k, v, who in await contact_owner.owners(session, text) if who.kind == "unknown"}
    events: list[dict] = []
    for c in contacts:
        if not isinstance(c, dict) or not isinstance(c.get("ref"), str):
            continue
        raw = restore(c["ref"])
        value = normalize_phone(raw) if c["ref"].startswith("[PHONE") else raw.strip().lower()
        kind = unknown.get(value or "")
        owner = str(c.get("owner") or "").lower()
        if not kind:
            continue
        if owner == "self":
            events.append({"key": kind, "status": await identity.save_contact_from_ai(session, profile, kind, value)})
        elif (relation := family_svc.relation_of(owner)) and relation not in ("other", "organization", "organisation"):
            events.append({"key": f"family:{relation}:{kind}",
                           "status": await family_svc.upsert(session, profile, {"relation": relation, "details": {kind: value}})})
        else:
            events.append({"key": kind, "status": f"not the guest's ({owner or 'unknown'})"})
    return events


async def _extract_and_save(session: AsyncSession, llm: LLMService, profile: Profile, conversation: Conversation,
                            channel: str | None, is_test: bool, content: str, system: str) -> tuple[list[dict], dict, object]:
    known = (profile.name,) if profile.name else ()

    def build(policy):
        g = PrivacyGuard(policy)
        return g.prepare_task(system.replace("{keys}", extraction_keys_hint()), content,
                              redactor=g.new_redactor(known_names=known))
    try:
        out = await llm.run(session, "extraction", build, channel=channel, conversation_id=conversation.id, is_test=is_test)
    except LLMUnavailable:
        return [], {}, None
    data = parse_json_object(out.result.text)
    if not data:
        return [], {}, None
    restore = out.request.redactor.restore  # "[ADDRESS_1]" back to what the guest wrote

    def clean(v):
        # The guest's own name reaches the model as [NAME_1] and comes back as "[NAME_1]" or "NAME_1": that is a value
        # we already have (and would restore to the first name only), never a new one. Drop it.
        if not isinstance(v, str):
            return v
        return None if _NAME_PLACEHOLDER.search(v) else restore(v)
    events: list[dict] = []
    for k in PROFILE_KEYS:
        v = clean(data.get(k))
        if k == "name" and profile.name:  # the guest's name hides as [NAME_1], so another name in the chat
            continue                      # ("amar ammur nam Rahima") looks like theirs: never replace a known name
        if v not in (None, "") and str(v) != str(getattr(profile, k, None)):
            events.append({"key": k, "status": await consent_svc.apply_field(session, profile, k, v)})
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    for k, v in list(facts.items())[:12]:
        v = clean(v)
        if k == "blood_group" and v:
            v = family_svc.blood_group(v) or v
        if v in (None, "", "null"):
            continue
        events.append({"key": str(k), "status": await consent_svc.apply_field(session, profile, str(k), v)})
    for rel in (data.get("family") if isinstance(data.get("family"), list) else [])[:10]:
        if isinstance(rel, dict):
            rel = {k: clean(v) for k, v in rel.items()}
            if isinstance(rel.get("details"), dict):
                rel["details"] = {k: clean(v) for k, v in rel["details"].items()}
            events.append({"key": f"family:{rel.get('relation')}", "status": await family_svc.upsert(session, profile, rel)})
    return events, data, restore


async def refresh_profile_summary(session: AsyncSession, profile: Profile) -> None:
    if profile.summary_dirty and not profile.erased_at:
        view = await load_profile_view(session, profile)
        profile.summary = compose_summary(view, PlainFormatter())  # dashboard copy; prompts rebuild it per request
        profile.summary_dirty = False


async def roll_conversation_summary(session: AsyncSession, llm: LLMService, conv: Conversation, channel: str | None, is_test: bool = False) -> bool:
    every = int(await get_setting(session, "summarize_every_n_messages") or 8)
    keep = int(await get_setting(session, "history_len") or 4)
    after = conv.summary_upto_message_id or 0
    msgs = (await session.execute(select(Message).where(Message.conversation_id == conv.id, Message.id > after,
            Message.role.in_(("user", "assistant", "agent"))).order_by(Message.id))).scalars().all()
    older = msgs[:-keep] if keep else msgs
    if len(older) < every:
        return False
    lines = "\n".join(f"{'U' if m.role == 'user' else 'A'}: {m.content_redacted}" for m in older)
    content = f"PREVIOUS SUMMARY: {conv.summary or '(none)'}\nNEW MESSAGES:\n{lines}"

    def build(policy):
        g = PrivacyGuard(policy)
        return g.prepare_task(SUMMARY_SYSTEM, content)
    try:
        out = await llm.run(session, "summarization", build, channel=channel, conversation_id=conv.id, is_test=is_test)
    except LLMUnavailable:
        return False
    conv.summary, conv.summary_upto_message_id = out.result.text[:700], older[-1].id
    return True


async def run_post_reply(session: AsyncSession, llm: LLMService, profile: Profile, conv: Conversation, channel: str | None,
                         is_test: bool = False, *, new_text: str | None = None, ai_replied: bool = True) -> dict:
    """new_text: what the guest wrote this turn (a burst counts as one). Guest info is collected from every turn,
    whoever answered it; the rolling summary only follows AI answers."""
    result: dict = {}
    if await get_setting(session, "info_collection_enabled"):
        if new_text and new_text.strip():
            result["info"] = await collect_guest_info(session, llm, profile, conv, channel, new_text, is_test)
    else:  # the older way: the latest messages as a whole, every N messages
        n_user = (await session.execute(select(func.count()).select_from(Message).where(
            Message.conversation_id == conv.id, Message.role == "user"))).scalar_one()
        every = int(await get_setting(session, "extraction_every_n_messages") or 4)
        if ai_replied and every > 0 and n_user % every == 0:
            result["extracted"] = await extract_profile(session, llm, profile, conv, channel, is_test)
    if ai_replied:
        result["summarized"] = await roll_conversation_summary(session, llm, conv, channel, is_test)
    await refresh_profile_summary(session, profile)
    return result
