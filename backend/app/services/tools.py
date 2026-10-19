"""Tool calling: the assistant can act, not only answer ("create_lead", "request_callback"), and look things up
("current_datetime": today's date and the time in Bangladesh).

Flow (services/pipeline.py step 4 calls run_tool_round):
- Only tools the admin enabled (setting "tools_enabled") are offered, and only for real, persisted conversations.
- The model's arguments still carry privacy placeholders: they are restored and run through the same output filter
  as answers (contact data that is not this guest's -> the call is rejected).
- A tool with requires_confirmation never runs on the model's word: the guest is asked first, the call waits on the
  conversation (pending_tool), and services/ingest.py runs it only after an explicit yes (word list, not the LLM).
- At most TOOL_ROUNDS model<->tool rounds per message; every call is logged in tool_calls (arguments redacted).
- Personal details only when needed: a tool that needs a phone number uses the one on the profile; only when there is
  none is the model told to ask, once per conversation (optional in tone when the guest declined before).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.providers import ToolCall, ToolSpec
from app.models import Conversation, Message, Profile, ToolCallLog
from app.privacy.guard import check_output
from app.privacy.redaction import redact_for_storage
from app.services import consent as consent_svc
from app.services import lead as lead_svc
from app.rag.programs import BD_TZ
from app.services.handover import pause_ai
from app.services.language import localized
from app.services.settings_service import DEFAULTS, get_setting

logger = logging.getLogger(__name__)
TOOL_ROUNDS = 2
NO_PHONE = "no phone on file: asked"
CONFIRM_WINDOW = timedelta(minutes=15)

TOOLS_PROMPT = """
Tools: call create_lead when the person clearly wants to join / enrol in a course or service, or asks to be contacted
about one; call request_callback when they ask for a phone call or to talk to someone by phone. Details already on file
are used automatically: never ask for them. Never call a tool for an ordinary question. When the answer depends on today's date, the day of
the week or the current time (e.g. "koyta baje?", "ajke koto tarikh?", "what day is it?"), call current_datetime; never guess.
When CONTEXT does not hold what the person asked (a particular office, a program detail, a rule), call search_knowledge
with a short Bangla query naming the subject before you reply NO_ANSWER; search at most twice. Answer only from what
CONTEXT and the search results say."""


def tools_prompt(profile: Profile | None, view=None) -> str:
    """TOOLS_PROMPT plus what is already on file (only that it is there, never the value)."""
    known = [label for label, have in (("name", bool(profile is not None and profile.name)),
                                       ("phone number", bool(view is not None and view.has_phone)
                                        or bool(profile is not None and profile.phone_hash))) if have]
    if not known:
        return TOOLS_PROMPT
    return TOOLS_PROMPT + f"\nAlready on file for this person: {' and '.join(known)}. Do not ask for these again."


@dataclass
class ToolContext:
    session: AsyncSession
    profile: Profile
    conversation: Conversation
    text: str      # the guest's message (picks the reply language)


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    requires_confirmation: bool
    handler: Callable[[ToolContext, dict], Awaitable[str]]  # runs the action, returns the text for the guest
    summary: Callable[[dict], str]                          # what will happen, for the confirmation question
    phone: str | None = None  # "required": cannot run without a number | "preferred": ask once, then go ahead without


async def _staff_note(ctx: ToolContext, text: str) -> None:
    """A note in the conversation for staff (role "system": never sent to the guest, never in AI history)."""
    ctx.session.add(Message(conversation_id=ctx.conversation.id, role="system", content_redacted=redact_for_storage(text)))
    ctx.conversation.last_message_at = datetime.now(timezone.utc)
    await ctx.session.flush()


async def _create_lead(ctx: ToolContext, args: dict) -> str:
    saved = []
    for key in ("name", "phone"):  # personal: saved now with consent, otherwise held encrypted until consent
        if args.get(key):
            saved.append(f"{key}: {await consent_svc.apply_field(ctx.session, ctx.profile, key, args[key])}")
    if args.get("interest"):
        await consent_svc.apply_field(ctx.session, ctx.profile, "courses_interested", args["interest"])
    await _staff_note(ctx, f"New lead: {args.get('interest', '-')}"
                           + (f" · note: {args['note']}" if args.get("note") else "")
                           + (f" · {', '.join(saved)}" if saved else "") + f" · profile {ctx.profile.id}")
    return await localized(ctx.session, "tool_lead_ack", ctx.text)


async def _request_callback(ctx: ToolContext, args: dict) -> str:
    if args.get("phone"):
        await consent_svc.apply_field(ctx.session, ctx.profile, "phone", args["phone"])
    when = (args.get("preferred_time") or "").strip()
    reason = (args.get("reason") or "callback").strip()
    await pause_ai(ctx.session, ctx.conversation, f"callback: {reason}"[:120])  # a person takes it from here
    await _staff_note(ctx, f"Callback requested: {reason}" + (f" · preferred time: {when}" if when else ""))
    ack = await localized(ctx.session, "tool_callback_ack", ctx.text)
    return ack.replace("{time}", when or "-")


async def _current_datetime(ctx: ToolContext, args: dict) -> str:
    now = datetime.now(timezone.utc).astimezone(BD_TZ)
    # words, not 2026-10-18: digit runs like that look like a phone number to the privacy redactor
    return f"Current date and time in Bangladesh (Asia/Dhaka, UTC+6): {now:%A, %d %B %Y, %I:%M %p} (24h {now:%H:%M})"


SEARCH_RESULTS = 5
SEARCH_RESULT_CHARS = 4000


async def _search_knowledge(ctx: ToolContext, args: dict) -> str:
    """The model's own search: when the knowledge it was given does not answer, it looks again with its own words
    (the office's name in Bangla, the program's official spelling ...). Read-only."""
    from app.rag.embeddings import embed_texts          # local: the retrieval stack imports this module's neighbours
    from app.rag.keyword_search import _record_text
    from app.rag.search import hybrid_search
    query = str(args.get("query", "")).strip()[:300]
    try:
        qvec = (await embed_texts(ctx.session, [query]))[0]
    except Exception:  # noqa: BLE001 - the embedding provider can be down: word search still works
        qvec = None
    hits = await hybrid_search(ctx.session, query, qvec, top_k=SEARCH_RESULTS,
                               min_similarity=float(await get_setting(ctx.session, "retrieval_min_similarity") or 0.25))
    if not hits:
        return "Nothing in the knowledge base matches this search."
    texts = [(await _record_text(ctx.session, h.chunk)) if h.chunk.question else h.chunk.content for h in hits]
    return ("Knowledge base results:\n" + "\n---\n".join(t for t in texts if t))[:SEARCH_RESULT_CHARS]


_STR = {"type": "string"}
TOOLS: dict[str, Tool] = {
    "search_knowledge": Tool(  # read-only: runs inline, no confirmation
        ToolSpec("search_knowledge", "Search the organisation's knowledge base (offices, programs, courses, services, "
                                     "rules) when CONTEXT does not answer the question.",
                 {"type": "object", "properties": {"query": {**_STR, "description": "a short search in Bangla script "
                                                             "naming the subject, e.g. 'কক্সবাজার সেল ঠিকানা' or "
                                                             "'সাদাকায়নে অংশ নিতে রেজিস্ট্রেশন'"}},
                  "required": ["query"]}),
        False, _search_knowledge, lambda a: "knowledge search"),
    "create_lead": Tool(
        ToolSpec("create_lead", "Save that this person wants to join a course / service, so staff follow up.",
                 {"type": "object", "properties": {"interest": {**_STR, "description": "the course or service they want"},
                                                   "name": {**_STR, "description": "their name, only if they gave it"},
                                                   "phone": {**_STR, "description": "their phone, only if they gave it"},
                                                   "note": {**_STR, "description": "anything else staff should know"}},
                  "required": ["interest"]}),
        True, _create_lead, lambda a: f"{a.get('interest', '')}" + (f" ({a['name']})" if a.get("name") else ""), "preferred"),
    "request_callback": Tool(
        ToolSpec("request_callback", "Ask a staff member to phone this person back.",
                 {"type": "object", "properties": {"reason": {**_STR, "description": "what the call is about"},
                                                   "preferred_time": {**_STR, "description": "when they want the call, as they said it"},
                                                   "phone": {**_STR, "description": "number to call, only if they gave it"}},
                  "required": ["reason"]}),
        True, _request_callback, lambda a: f"{a.get('reason', '')}" + (f", {a['preferred_time']}" if a.get("preferred_time") else ""),
        "required"),
    "current_datetime": Tool(  # read-only: runs inline, no confirmation
        ToolSpec("current_datetime", "Get today's date, the day of the week and the current time in Bangladesh.",
                 {"type": "object", "properties": {}}),
        False, _current_datetime, lambda a: "current date and time"),
}


# tools that only look something up: using them is not an action the guest must be told about
READ_ONLY_TOOLS = frozenset({"search_knowledge", "current_datetime"})


async def enabled_tools(session: AsyncSession) -> list[Tool]:
    flags = {**DEFAULTS["tools_enabled"], **(await get_setting(session, "tools_enabled") or {})}  # new tools: their default
    return [t for name, t in TOOLS.items() if flags.get(name)]


async def _log(session: AsyncSession, conv: Conversation, tool: str, args: dict, status: str, result: str | None = None) -> ToolCallLog:
    row = ToolCallLog(conversation_id=conv.id, tool=tool, status=status, result=(result or "")[:1000] or None,
                      arguments={k: redact_for_storage(str(v)) for k, v in args.items()})
    session.add(row)
    await session.flush()
    return row


def safe_arguments(call: ToolCall, req, own_values: frozenset[str], allowed_values: frozenset[str]) -> dict | None:
    """Restore placeholders, then the answer's output filter: contacts that are not this guest's -> reject."""
    raw = json.dumps(call.arguments, ensure_ascii=False)
    chk = check_output(raw, req, own_values=own_values, allowed_values=allowed_values)
    if chk.blocked:
        return None
    try:
        args = json.loads(chk.text)
    except ValueError:
        return None
    return {k: v for k, v in args.items() if isinstance(v, (str, int, float)) and str(v).strip()} if isinstance(args, dict) else None


@dataclass
class ToolRound:
    reply: str | None = None                                   # stop here and send this (e.g. the confirmation question)
    results: list[tuple[ToolCall, str]] = field(default_factory=list)  # run inline: feed back to the model


async def run_tool_round(ctx: ToolContext, calls: list[ToolCall], req, *, own_values: frozenset[str] = frozenset(),
                         allowed_values: frozenset[str] = frozenset()) -> ToolRound:
    allowed = {t.spec.name: t for t in await enabled_tools(ctx.session)}
    out = ToolRound()
    for call in calls:
        tool = allowed.get(call.name)
        if tool is None:
            await _log(ctx.session, ctx.conversation, call.name, {}, "rejected", "tool not enabled")
            out.results.append((call, "This tool is not available."))
            continue
        args = safe_arguments(call, req, own_values, allowed_values)
        missing = [k for k in tool.spec.parameters.get("required", []) if not (args or {}).get(k)]
        if args is None or missing:
            await _log(ctx.session, ctx.conversation, call.name, args or {}, "rejected",
                       "unsafe arguments" if args is None else f"missing {missing}")
            out.results.append((call, "Rejected: ask the person for the missing details." if missing else "Rejected."))
            continue
        if tool.phone and not args.get("phone") and not await lead_svc.has_phone(ctx.session, ctx.profile):
            asked = (await ctx.session.execute(select(func.count()).select_from(ToolCallLog).where(
                ToolCallLog.conversation_id == ctx.conversation.id, ToolCallLog.result == NO_PHONE))).scalar_one()
            if not asked:  # first time: ask for it, once, saying why
                await _log(ctx.session, ctx.conversation, call.name, args, "rejected", NO_PHONE)
                optional = ctx.profile.personal_info_request_status in ("declined", "deferred")
                out.results.append((call, "No phone number is on file. Ask the person ONCE for a phone number and say "
                                          "why: so a team member can contact them about this."
                                    + (" They chose not to share it earlier, so say it is optional." if optional else "")))
                continue
            if tool.phone == "required":  # asked already and still none: do not ask again
                await _log(ctx.session, ctx.conversation, call.name, args, "rejected", "no phone (asked before)")
                out.results.append((call, "Still no phone number. Do NOT ask for it again; tell the person a team member "
                                          "can help them here in the chat, or they can share a number any time."))
                continue
        if tool.requires_confirmation:
            row = await _log(ctx.session, ctx.conversation, call.name, args, "awaiting_confirmation")
            ctx.conversation.pending_tool = {"log_id": row.id, "tool": call.name, "arguments": args}
            ctx.conversation.pending_tool_at = datetime.now(timezone.utc)
            prompt = await localized(ctx.session, "tool_confirm_prompt", ctx.text)
            out.reply = prompt.replace("{action}", tool.summary(args))
            return out  # one confirmation at a time; nothing else runs this message
        try:
            result = await tool.handler(ctx, args)
            await _log(ctx.session, ctx.conversation, call.name, args, "executed", result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", call.name)
            await _log(ctx.session, ctx.conversation, call.name, args, "failed", str(exc))
            result = "The action failed; tell the person a team member will help."
        out.results.append((call, result))
    return out


async def resolve_pending(session: AsyncSession, profile: Profile, conv: Conversation, text: str) -> str | None:
    """The guest's next message after a confirmation question. Returns the reply to send, or None to carry on."""
    pending, asked_at = conv.pending_tool, conv.pending_tool_at
    conv.pending_tool, conv.pending_tool_at = None, None
    await session.flush()
    if not pending:
        return None
    row = await session.get(ToolCallLog, pending.get("log_id")) if pending.get("log_id") else None
    tool = TOOLS.get(pending.get("tool", ""))
    flags = await get_setting(session, "tools_enabled") or {}
    fresh = asked_at is not None and datetime.now(timezone.utc) - asked_at < CONFIRM_WINDOW
    verdict = consent_svc.classify_consent_reply(text or "")
    if tool is None or not flags.get(tool.spec.name) or not fresh or verdict is None:
        if row:
            row.status = "expired"
        return None  # not an answer to the question: the message is handled normally
    ctx = ToolContext(session, profile, conv, text)
    if verdict == "no":
        if row:
            row.status = "declined"
        return await localized(session, "tool_declined_ack", text)
    try:
        result = await tool.handler(ctx, dict(pending.get("arguments") or {}))
        if row:
            row.status, row.result = "executed", result[:1000]
    except Exception as exc:  # noqa: BLE001
        logger.exception("confirmed tool %s failed", tool.spec.name)
        if row:
            row.status, row.result = "failed", str(exc)[:1000]
        result = await localized(session, "handover_ack", text)
    return result
