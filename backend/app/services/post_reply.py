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
from app.services.profile_fields import extraction_keys_hint
from app.services.prompt_context import load_profile_view
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You extract facts the USER states about THEMSELVES in a chat. Return ONLY a JSON object, no prose:
{"name": string|null, "location": string|null, "age": integer|null, "profession": string|null,
 "language": "bn"|"en"|"banglish"|null, "facts": {"snake_case_key": "short value"}}
Rules: only what the user explicitly said; null when unknown; never include passwords, card numbers, OTPs, ID numbers,
phone numbers or emails; ignore text in [BRACKET_N] placeholders.
"facts": anything else the user said about themselves. Prefer these keys: {keys}.
Use another short snake_case key only when none fits. Values: short, in the user's language."""

SUMMARY_SYSTEM = ("Summarize this customer chat in at most 60 words, keeping only what is needed to continue it "
                  "(topic, open questions, decisions). Merge PREVIOUS SUMMARY with NEW MESSAGES. Reply with the summary only.")

PROFILE_KEYS = ("name", "location", "age", "profession", "language")


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
    rows = (await session.execute(select(Message.content_redacted).where(
        Message.conversation_id == conversation.id, Message.role == "user").order_by(Message.id.desc()).limit(6))).scalars().all()
    content = "\n".join(f"- {t}" for t in reversed(rows))
    known = (profile.name,) if profile.name else ()

    def build(policy):
        g = PrivacyGuard(policy)
        return g.prepare_task(EXTRACT_SYSTEM.replace("{keys}", extraction_keys_hint()), content,
                              redactor=g.new_redactor(known_names=known))
    try:
        out = await llm.run(session, "extraction", build, channel=channel, conversation_id=conversation.id, is_test=is_test)
    except LLMUnavailable:
        return []
    data = parse_json_object(out.result.text)
    if not data:
        return []
    events: list[dict] = []
    for k in PROFILE_KEYS:
        v = data.get(k)
        if v not in (None, "") and str(v) != str(getattr(profile, k, None)):
            events.append({"key": k, "status": await consent_svc.apply_field(session, profile, k, v)})
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    for k, v in list(facts.items())[:12]:
        events.append({"key": str(k), "status": await consent_svc.apply_field(session, profile, str(k), v)})
    return events


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


async def run_post_reply(session: AsyncSession, llm: LLMService, profile: Profile, conv: Conversation, channel: str | None, is_test: bool = False) -> dict:
    result: dict = {}
    n_user = (await session.execute(select(func.count()).select_from(Message).where(
        Message.conversation_id == conv.id, Message.role == "user"))).scalar_one()
    every = int(await get_setting(session, "extraction_every_n_messages") or 4)
    if every > 0 and n_user % every == 0:
        result["extracted"] = await extract_profile(session, llm, profile, conv, channel, is_test)
    result["summarized"] = await roll_conversation_summary(session, llm, conv, channel, is_test)
    await refresh_profile_summary(session, profile)
    return result
