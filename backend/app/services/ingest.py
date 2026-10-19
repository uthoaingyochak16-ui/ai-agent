"""Worker-side processing of one normalized incoming message."""
from __future__ import annotations

import asyncio
import re
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.base import IncomingMessage, OutOfWindowError
from app.channels.registry import get_adapter
from app.models import ChannelIdentity, Conversation, Message, Variable, WebhookEvent
from app.rag.variables import match_variable
from app.core.logging import conversation_id_var, event_id_var
from app.privacy.redaction import redact_for_storage
from app.services.contact_owner import public_contacts
from app.services import consent as consent_svc
from app.privacy.summary import PlainFormatter, compose_summary
from app.services import inspector
from app.services import lead as lead_svc
from app.services import program_followup
from app.services import security
from app.services.prompt_context import load_profile_view
from app.services.reply_style import apply_style, classify
from app.services.handover import detect_handover, maybe_auto_resume, pause_ai
from app.services.identity import Resolved, handle_shared_contacts, link_same_person, resolve
from app.services.greeting import greeting_reply
from app.services.continuation import reply_to_our_question
from app.services.smalltalk import smalltalk_reply
from app.services.pipeline import is_org_specific, match_qa_text
from app.services.outbound import (TEST_PREFIX, ChannelNotConfigured, SendFailed, get_credentials,
                                   send_to_conversation)
from app.services.post_reply import run_post_reply
from app.services.reply_split import split_reply
from app.services.reply_engine import generate_reply, get_llm_service
from app.services.router import RouterDecision, route_intent
from app.services.tools import resolve_pending
from app.services.language import learn_preference, localized
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
PROFILE_REFRESH = timedelta(hours=24)
TYPING_REFRESH_S = 15
TYPING_MAX_REFRESHES = 8  # ~2 min, the job timeout
ERASED_TEXT = "[deleted at user's request]"  # what consent.erase_profile leaves in place of message text
OLDER_EVENTS_WAIT_S = 8   # the replying job waits this long for older messages of the burst to be stored
EVENT_LOCK_TIMEOUT_S = 60.0
EVENT_LOCK_POLL_S = 0.1
EVENT_ORDER_RETRY_S = 1.0
ERASURE_CONFIRM_WINDOW = timedelta(minutes=15)  # how long a router-asked "reply YES to delete" stays valid


async def _enqueue_event_retry(queue, event_id: int, reason: str) -> None:
    if queue is None:
        return
    try:
        await queue.enqueue_job("process_event_job", event_id,
                                _job_id=f"evt-{event_id}-{reason}-{time.time_ns()}",
                                _defer_by=timedelta(seconds=EVENT_ORDER_RETRY_S))
    except Exception:  # noqa: BLE001 - the durable queued row is also picked up by the cron
        logger.exception("could not re-enqueue deferred event %s", event_id)


async def _set_queued(maker: async_sessionmaker[AsyncSession], event_id: int) -> None:
    async with maker() as session:
        await session.execute(update(WebhookEvent).where(
            WebhookEvent.id == event_id, WebhookEvent.status == "queued").values(status="queued"))
        await session.commit()


async def _acquire_guest_lock(conn, key: str) -> bool:
    """Session-level lock on a dedicated connection, so commits in _respond cannot release it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + EVENT_LOCK_TIMEOUT_S
    while True:
        got = await conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": key})
        await conn.commit()  # session locks survive this; avoid holding an idle transaction during the LLM call
        if got:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(min(EVENT_LOCK_POLL_S, max(0, deadline - loop.time())))


async def _has_older_pending(session: AsyncSession, ev: WebhookEvent, sender: str) -> bool:
    same_sender = or_(WebhookEvent.sender_id == sender,
                      and_(WebhookEvent.sender_id.is_(None), WebhookEvent.payload["external_user_id"].astext == sender))
    return (await session.execute(select(WebhookEvent.id).where(
        WebhookEvent.channel == ev.channel, same_sender, WebhookEvent.id < ev.id,
        WebhookEvent.status.in_(("queued", "processing"))).limit(1))).first() is not None


async def process_event(maker: async_sessionmaker[AsyncSession], event_id: int, queue=None) -> str:
    """Process one durable event. A guest's lock spans every commit and outbound/LLM call."""
    async with maker() as peek:
        ev = await peek.get(WebhookEvent, event_id)
        if ev is None or ev.status in ("done", "processing"):
            return "skipped"
        msg = IncomingMessage.from_json(ev.payload)
        lock_key = f"{msg.channel}\x1f{msg.external_user_id}"
        engine = peek.bind

    async with engine.connect() as lock_conn:
        acquired = await _acquire_guest_lock(lock_conn, lock_key)
        if not acquired:
            await _set_queued(maker, event_id)
            await _enqueue_event_retry(queue, event_id, "lock")
            return "queued"
        try:
            return await _process_event_locked(maker, event_id, queue)
        finally:
            try:
                await lock_conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": lock_key})
                await lock_conn.commit()
            except Exception:  # noqa: BLE001 - closing the dedicated connection also releases session locks
                logger.exception("could not explicitly release event lock for %s", event_id)


async def _process_event_locked(maker: async_sessionmaker[AsyncSession], event_id: int, queue=None) -> str:
    """The caller holds the per-channel-user advisory lock."""
    tokens = (event_id_var.set(event_id), conversation_id_var.set(None))
    try:
        return await _process_event_inner(maker, event_id, queue)
    finally:
        conversation_id_var.reset(tokens[1])
        event_id_var.reset(tokens[0])


async def _process_event_inner(maker: async_sessionmaker[AsyncSession], event_id: int, queue=None) -> str:
    async with maker() as session:
        ev = (await session.execute(select(WebhookEvent).where(WebhookEvent.id == event_id)
                                    .with_for_update(skip_locked=True))).scalar_one_or_none()
        if ev is None or ev.status in ("done", "processing"):
            return "skipped"
        msg = IncomingMessage.from_json(ev.payload)
        sender = ev.sender_id or msg.external_user_id
        if await _has_older_pending(session, ev, sender):
            await session.commit()
            await _enqueue_event_retry(queue, event_id, "order")
            return "queued"
        ev.status, ev.attempts = "processing", ev.attempts + 1
        await session.commit()
        try:
            plan = await _debounce_plan(session, ev, msg)
            if plan == "combine":
                await _wait_for_older(session, ev, msg)
            await handle_message(session, msg, reply=plan != "hold", combine=plan == "combine")
            ev.status, ev.error = "done", None
            ev.processed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            ev = await session.get(WebhookEvent, event_id)
            ev.status = "failed" if ev.attempts >= MAX_ATTEMPTS else "queued"
            ev.error = f"{type(exc).__name__}: {exc}"[:500]
            await session.commit()
            logger.exception("event %s failed", event_id)
        return ev.status


async def handle_message(session: AsyncSession, msg: IncomingMessage, llm=None, *, reply: bool = True,
                         combine: bool = False) -> Resolved | None:
    """reply=False: a newer message of the same burst will answer; store this one and run only the checks that must
    see every single message (delete my data, consent, contacts, handover). combine=True: answer the whole burst."""
    adapter = get_adapter(msg.channel)
    r = await resolve(session, msg)
    conversation_id_var.set(r.conversation.id)  # every later log line of this message carries it

    already = (await session.execute(select(Message.id).where(
        Message.conversation_id == r.conversation.id, Message.role == "user",
        Message.external_message_id == msg.message_id))).first()
    if already:  # a retry after a crash between "store" and "done"
        return r

    user_msg = Message(conversation_id=r.conversation.id, role="user", content_redacted=redact_for_storage(msg.text, keep=await public_contacts(session)),
                       attachments=msg.attachments or None, external_message_id=msg.message_id)
    session.add(user_msg)
    r.conversation.last_message_at = datetime.now(timezone.utc)
    await session.flush()
    if lang := await learn_preference(session, r.profile):
        r.events.append(f"preferred language: {lang}")
    if msg.text and security.contains_card(msg.text):  # stored hidden already; flag it, warn the guest, alert staff
        user_msg.flag = security.CARD_SHARED
        r.notice = await localized(session, "card_warning", msg.text)
        r.events.append("card number shared: hidden, guest warned, alert raised")
        logger.warning("guest sent a card number (conversation %s)", r.conversation.id)

    creds = None          # reading the channel (seen, name/photo): works even with replies switched off
    replies_on = False    # sending (typing bubble)
    if not r.identity.external_id.startswith(TEST_PREFIX):
        try:
            creds = await get_credentials(session, msg.channel, account_id=msg.recipient_id, for_sending=False)
            await get_credentials(session, msg.channel, account_id=msg.recipient_id)
            replies_on = True
        except ChannelNotConfigured:
            pass
    if creds is not None:
        try:  # best effort: read receipt
            await adapter.mark_read(msg.external_user_id, msg.message_id, creds)
        except Exception:  # noqa: BLE001
            pass
        await _refresh_profile(adapter, r.identity, creds)
    try:  # one guest = one profile: link this new chat account to the guest's existing profile if it is them
        await link_same_person(session, r, adapter, creds)
    except Exception:  # noqa: BLE001 - never block the reply
        logger.exception("identity linking failed")

    if await maybe_auto_resume(session, r.conversation, float(await get_setting(session, "ai_auto_resume_minutes") or 0)):
        r.events.append("AI resumed automatically (staff quiet)")

    if not reply:
        await _per_message_checks(session, r, msg)
        r.events.append("held: a newer message of this burst will be answered")
        await session.commit()
        return r

    # typing bubble while we work on the answer; not when a person has taken over (nobody is replying yet)
    typing = _Typing(adapter, msg, creds) if replies_on and not r.conversation.ai_paused else None
    try:
        ai_replied = await _respond(session, r, msg, llm, combine=combine)
    finally:
        if typing:
            typing.stop()

    # background-ish work after the user already has their answer; failures never affect the conversation.
    # Guest info is collected from every turn (also greetings, lead replies, handover); the summary only after AI answers.
    try:
        r.post = await run_post_reply(session, llm or get_llm_service(session), r.profile, r.conversation, msg.channel,
                                       is_test=r.identity.external_id.startswith(TEST_PREFIX),
                                       new_text=r.turn_text or msg.text, ai_replied=ai_replied)
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("post-reply tasks failed")
    return r


async def _respond(session: AsyncSession, r: Resolved, msg: IncomingMessage, llm, *, combine: bool = False) -> bool:
    """Steps 1-5. Returns True when the AI answered (post-reply extraction should run)."""
    if await _per_message_checks(session, r, msg):
        return False
    # the question: this message, or every message of the burst since the last reply ("hi" / "course er" / "fee koto?")
    question, turns = await _burst_text(session, r, msg) if combine else (msg.text or "", 1)
    r.turn_text = question
    if turns > 1:
        r.events.append(f"answered {turns} messages together")
    if r.conversation.ai_paused:
        # A human still owns the conversation, but deterministic answers from saved Q&A/cache or a
        # strongly matching knowledge excerpt remain useful. Unknown questions stay silent for staff.
        reply = await generate_reply(session, r, question, llm=llm, knowledge_only=True, combined_turns=turns)
        r.debug = reply.debug
        if reply.path in ("qa_shortcut", "cache", "kb_extract", "program"):
            r.events.append(f"trusted answer during handover: {reply.path}")
            await _say(session, r, reply.text, sources=reply.sources)
            await session.commit()
        return False

    # 5) "are you saving my data?" -> yes, and why (never said unless asked); while we wait for details, lead does it
    if r.conversation.lead_awaiting_at is None and lead_svc.asks_about_data(question):
        r.events.append("asked about data saving")
        await _say(session, r, await lead_svc.data_answer(session, r, question), sources={"path": "data_question"})
        await session.commit()
        return False

    # 5'') we asked "which district?" after a program answer: a district gets the programs there (exact match only)
    nearby = await program_followup.before_answer(session, r, question)
    if nearby:
        r.debug = {"path": "district", "steps": ["district answer: programs looked up in the Programs table"]}
        await _say(session, r, nearby, sources={"path": "district"})
        await session.commit()
        return False

    # 5') name / phone first: ask once (holding the question), or save the details the guest just sent
    ask, question = await lead_svc.before_answer(session, llm or get_llm_service(session), r, question)
    if ask:
        r.debug = {"path": "lead", "steps": []}
        await _say(session, r, ask, sources={"path": "lead"})
        await session.commit()
        return False

    # 5a) bare greeting -> canned welcome (no AI call). A greeting is never an answer to our question.
    greet = await greeting_reply(session, r, question)
    if greet:
        r.events.append("greeting reply")
        r.debug = {"path": "greeting", "steps": []}
        greet, extra, _ = await apply_style(session, "greeting", question, greet)
        r.followups += extra
        await _say(session, r, greet, sources={"path": "greeting"})
        await session.commit()
        return False

    # 5b) everyday small talk ("who are you?", "kemon achen") -> ready answer (no AI call)
    # ...but "ji" / "Ok" right after the assistant asked something answers that question: not small talk ("Ok" ->
    # "Sure 🙂" would drop the conversation). The answer step continues from the question.
    answering = reply_to_our_question(question, await _recent_history(session, r, skip_user_turns=turns))
    if answering:
        r.events.append("reply to the assistant's question: conversation continued")
    # a message that names the organisation or its services ("Quantum method ki?") is a real question, not small talk
    chat = None if answering or is_org_specific(question) else await smalltalk_reply(session, question)
    if chat:
        r.events.append("small talk reply")
        r.debug = {"path": "smalltalk", "steps": []}
        await _say(session, r, chat, sources={"path": "smalltalk"})
        await session.commit()
        return False

    # 5c) intent router: only when no free (0-token) answer exists and it is switched on; None = today's rules
    decision = None
    if await get_setting(session, "router_enabled") and not await _free_answer_exists(session, question):
        decision = await route_intent(
            session, llm or get_llm_service(session), text=question,
            history=await _recent_history(session, r, skip_user_turns=turns),
            consent_asked=consent_svc.consent_state(r.profile) == "asked", channel=r.identity.channel,
            conversation_id=r.conversation.id, is_test=r.identity.external_id.startswith(TEST_PREFIX))
        if decision is not None:
            r.events.append(f"router: {decision.intent}" + (" (needs a person)" if decision.needs_human else ""))
            if await _apply_router(session, r, question, decision):
                r.debug = {"path": f"router_{decision.intent}", "router": decision.as_debug()}
                return False

    # 5d) AI reply
    reply = await generate_reply(session, r, question, llm=llm, combined_turns=turns,
                                 standalone_question=decision.standalone_question if decision else None,
                                 guest_request=decision.request if decision else None,
                                 guest_language=decision.language if decision else None)
    r.debug = reply.debug
    if decision is not None:
        r.debug["router"] = decision.as_debug()
    # reply style for this question type, then the program follow-up bubble (each only where it applies)
    r.qtype = classify(question, reply.path, reply.debug)
    text, extra, rule = await apply_style(session, r.qtype, question, reply.text)
    r.followups += extra + await program_followup.after_answer(session, r, question, r.qtype)
    if reply.path == "payment_disabled" and not r.conversation.ai_paused:
        # Payment questions are not answered by AI. Put the chat in the existing human handover queue so a staff
        # member can take ownership and respond in the customer's channel.
        await pause_ai(session, r.conversation, "payment_request")
        text = await localized(session, "handover_ack", question)
        r.events.append("payment question: handed over to human agent")
    if rule == "auto" and not r.conversation.ai_paused:
        await pause_ai(session, r.conversation, f"style:{r.qtype}")
        r.followups.append(await localized(session, "handover_ack", question))
        r.events.append(f"reply style for {r.qtype}: handed over")
    r.debug["inspector"] = await _inspect(session, r, question, text, r.debug)
    sources = {**(reply.sources or {}), "inspector": r.debug["inspector"]}
    await _say(session, r, text, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, sources=sources)
    if reply.path == "qa_shortcut" and _PROMISES_A_PERSON.search(reply.text) and not r.conversation.ai_paused:
        # a saved answer that says "I am letting a team member know" (crisis, complaint, refund, urgent blood ...):
        # make it true - the chat goes to the handover queue
        await pause_ai(session, r.conversation, f"qa:{(reply.debug.get('qa_match') or {}).get('id', '')}")
        r.events.append("saved answer promised a person: handed over")
    await session.commit()
    return True


async def _inspect(session: AsyncSession, r: Resolved, question: str, text: str, debug: dict) -> dict:
    """The reply inspector summary (Test chat panel, and stored with the reply for the Inbox)."""
    view = await load_profile_view(session, r.profile)
    facts = [x for x in compose_summary(view, PlainFormatter()).split("; ") if x]
    facts += [x for x, have in (("phone: on file", view.has_phone),) if have]
    return inspector.build(question=question, debug=debug, reply=text, qtype=r.qtype or classify(question, debug.get("path"), debug),
                           profile_facts=facts, followups=list(r.followups), paused=r.conversation.ai_paused)


_PROMISES_A_PERSON = re.compile(
    r"প্রতিনিধিকে\s+জানাচ্ছি|প্রতিনিধির\s+সাথে\s+যুক্ত\s+করছি|প্রতিনিধিকে\s+জানাব|তাঁকে\s+জানাচ্ছি|"
    r"(?i:letting\s+(a\s+team\s+member|them)\s+know|connecting\s+you\s+with\s+a\s+team\s+member|"
    r"informing\s+a\s+team\s+member|we\s+will\s+inform\s+a\s+team\s+member)")


async def _per_message_checks(session: AsyncSession, r: Resolved, msg: IncomingMessage) -> bool:
    """Steps 1-4, which must see every single message even inside a burst. True = handled (an ack was sent)."""
    # 0a) the assistant asked "shall I do X?" for a tool: only an explicit yes runs it (word list, never the LLM)
    if r.conversation.pending_tool:
        answer = await resolve_pending(session, r.profile, r.conversation, msg.text or "")
        if answer is not None:
            await session.commit()
            await _say(session, r, answer, sources={"path": "tool"})
            await session.commit()
            r.events.append("pending tool answered")
            return True

    # 0) the router asked "reply YES to delete": only an explicit yes (word list, never the LLM) erases
    if r.conversation.erasure_requested_at is not None:
        asked_at, r.conversation.erasure_requested_at = r.conversation.erasure_requested_at, None
        await session.flush()
        fresh = datetime.now(timezone.utc) - asked_at < ERASURE_CONFIRM_WINDOW
        if fresh and consent_svc.classify_consent_reply(msg.text or "") == "yes":
            await consent_svc.erase_profile(session, r.profile.id)
            await session.commit()
            await _say(session, r, await localized(session, "erasure_ack", msg.text))
            await session.commit()
            r.events.append("data erased after confirmation")
            return True
        r.events.append("erasure not confirmed")

    # 1) "delete my data"
    if msg.text and consent_svc.is_erasure_request(msg.text):
        await consent_svc.erase_profile(session, r.profile.id)
        await session.commit()
        await _say(session, r, await localized(session, "erasure_ack", msg.text))
        await session.commit()
        return True

    # 2) answer to a pending consent question
    if consent_svc.consent_state(r.profile) == "asked":
        verdict = consent_svc.classify_consent_reply(msg.text or "")
        if verdict:
            await consent_svc.record_consent(session, r.profile, verdict == "yes")
            r.events.append(f"consent {verdict}")

    # 3) phone/email shared -> link / merge / save
    if msg.text:
        await handle_shared_contacts(session, r, msg.text)
    await session.commit()

    # 4) human handover (keyword) or already paused
    if not r.conversation.ai_paused and msg.text:
        kw = detect_handover(msg.text, list(await get_setting(session, "handover_keywords") or []))
        if kw:
            await pause_ai(session, r.conversation, f"keyword:{kw}")
            await session.commit()
            r.debug = {"path": "handover", "steps": [f"handover keyword: {kw}"]}
            ack, extra, _ = await apply_style(session, "handover", msg.text, await localized(session, "handover_ack", msg.text))
            r.followups += extra
            await _say(session, r, ack, sources={"path": "handover"})
            await session.commit()
            return True
    return False


# ---- intent router -------------------------------------------------------------------------------------------------
async def _free_answer_exists(session: AsyncSession, question: str) -> bool:
    """Saved Q&A (wording match) or a Variable answers it: no need to spend tokens on routing."""
    qa, _ = await match_qa_text(session, question)
    if qa is not None:
        return True
    return match_variable(question, list((await session.execute(select(Variable))).scalars())) is not None


async def _recent_history(session: AsyncSession, r: Resolved, *, skip_user_turns: int = 1) -> list[tuple[str, str]]:
    rows = (await session.execute(select(Message).where(
        Message.conversation_id == r.conversation.id, Message.role.in_(("user", "assistant", "agent")))
        .order_by(Message.id.desc()).limit(4 + skip_user_turns))).scalars().all()
    history = [("assistant" if m.role == "agent" else m.role, m.content_redacted) for m in reversed(rows)]
    for _ in range(max(1, skip_user_turns)):  # the current message(s) are the question, not history
        if history and history[-1][0] == "user":
            history = history[:-1]
    return history


async def _apply_router(session: AsyncSession, r: Resolved, text: str, d: RouterDecision) -> bool:
    """Map a router decision onto the existing actions. True = replied; False = answer it as a question."""
    lang = "en" if d.language == "en" else "bn"  # Banglish readers get Bangla
    if d.needs_human or d.intent in ("handover_request", "complaint"):
        await pause_ai(session, r.conversation, f"router:{d.intent}")
        await session.commit()
        ack = "complaint_handover_ack" if d.intent == "complaint" else "handover_ack"   # a complaint is apologised for first
        await _say(session, r, await localized(session, ack, text, lang), sources={"path": "router_handover"})
        await session.commit()
        return True
    if d.intent == "erasure_request":  # not the exact keyword: confirm first, never erase on the model's word
        r.conversation.erasure_requested_at = datetime.now(timezone.utc)
        await session.commit()
        await _say(session, r, await localized(session, "erasure_confirm_prompt", text, lang), sources={"path": "router_erasure"})
        await session.commit()
        return True
    if d.intent in ("consent_yes", "consent_no") and consent_svc.consent_state(r.profile) == "asked":
        given = d.intent == "consent_yes"
        await consent_svc.record_consent(session, r.profile, given)
        await session.commit()
        await _say(session, r, await localized(session, "consent_yes_ack" if given else "consent_no_ack", text, lang),
                   sources={"path": "router_consent"})
        await session.commit()
        return True
    if d.intent in ("consent_yes", "consent_no"):  # "ji" / "ok" when nothing was asked: an acknowledgement
        ack = await smalltalk_reply(session, text) or await localized(session, "lead_anything_else", text, lang)
        await _say(session, r, ack, sources={"path": "smalltalk"})
        await session.commit()
        return True
    if d.intent == "greeting":
        greet = await greeting_reply(session, r, text, assume_greeting=True)
        if greet:
            await _say(session, r, greet, sources={"path": "greeting"})
            await session.commit()
            return True
    if d.intent == "smalltalk":
        chat = await smalltalk_reply(session, text)
        if chat:
            await _say(session, r, chat, sources={"path": "smalltalk"})
            await session.commit()
            return True
    return False  # question / other / consent when nothing was asked / small talk we have no answer for


# ---- debounce: one reply to a burst of messages ------------------------------------------------------------------
async def _debounce_plan(session: AsyncSession, ev: WebhookEvent, msg: IncomingMessage) -> str:
    """'single' (debounce off), 'hold' (a newer message of the same burst will answer) or 'combine' (answer the burst)."""
    if float(await get_setting(session, "debounce_seconds") or 0) <= 0 or msg.external_user_id.startswith(TEST_PREFIX):
        return "single"
    sender = ev.sender_id or msg.external_user_id
    newer = (await session.execute(select(WebhookEvent.id).where(
        WebhookEvent.channel == ev.channel, WebhookEvent.sender_id == sender, WebhookEvent.id > ev.id).limit(1))).first()
    if newer is None:
        return "combine"
    cap = timedelta(seconds=float(await get_setting(session, "debounce_max_seconds") or 10))
    started = await _burst_started_at(session, ev, msg)
    return "hold" if datetime.now(timezone.utc) - started < cap else "combine"  # never keep a guest waiting past the cap


async def _burst_started_at(session: AsyncSession, ev: WebhookEvent, msg: IncomingMessage) -> datetime:
    """Arrival of the oldest message not answered yet (this one if the guest got a reply just before)."""
    first_unanswered = None
    conv_id = (await session.execute(select(Conversation.id).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
                                     .where(ChannelIdentity.channel == msg.channel, ChannelIdentity.external_id == msg.external_user_id,
                                            Conversation.status != "closed").order_by(Conversation.id.desc()).limit(1))).scalar_one_or_none()
    if conv_id is not None:
        last_bot = (await session.execute(select(func.max(Message.id)).where(
            Message.conversation_id == conv_id, Message.role.in_(("assistant", "agent"))))).scalar_one_or_none() or 0
        first_unanswered = (await session.execute(select(func.min(Message.created_at)).where(
            Message.conversation_id == conv_id, Message.role == "user", Message.id > last_bot))).scalar_one_or_none()
    return min(t for t in (first_unanswered, ev.received_at) if t is not None)


async def _wait_for_older(session: AsyncSession, ev: WebhookEvent, msg: IncomingMessage) -> None:
    """Jobs can run in parallel: let the older messages of this burst be stored before the combined reply is built."""
    sender = ev.sender_id or msg.external_user_id
    loop = asyncio.get_running_loop()
    deadline = loop.time() + OLDER_EVENTS_WAIT_S
    while loop.time() < deadline:
        pending = (await session.execute(select(WebhookEvent.id).where(
            WebhookEvent.channel == ev.channel, WebhookEvent.sender_id == sender, WebhookEvent.id < ev.id,
            WebhookEvent.status.in_(("queued", "processing"))).limit(1))).first()
        await session.commit()  # end the read so the next poll sees other jobs' commits
        if pending is None:
            return
        await asyncio.sleep(0.3)


async def _burst_text(session: AsyncSession, r: Resolved, msg: IncomingMessage) -> tuple[str, int]:
    """Every user message since the last assistant/staff reply (within the burst window), joined with newlines.
    Raw text comes from the webhook inbox; the stored copy is redacted."""
    window = timedelta(seconds=float(await get_setting(session, "debounce_max_seconds") or 10)
                       + float(await get_setting(session, "debounce_seconds") or 0) + 30)
    last_bot = (await session.execute(select(func.max(Message.id)).where(
        Message.conversation_id == r.conversation.id, Message.role.in_(("assistant", "agent"))))).scalar_one_or_none() or 0
    rows = (await session.execute(select(Message).where(
        Message.conversation_id == r.conversation.id, Message.role == "user", Message.id > last_bot,
        Message.created_at >= datetime.now(timezone.utc) - window).order_by(Message.id))).scalars().all()
    rows = [m for m in rows if m.content_redacted != ERASED_TEXT]  # never bring back text the guest asked us to delete
    ext = [m.external_message_id for m in rows if m.external_message_id]
    raw = {e.external_id: (e.payload or {}).get("text") for e in (await session.execute(select(WebhookEvent).where(
        WebhookEvent.channel == msg.channel, WebhookEvent.external_id.in_(ext)))).scalars()} if ext else {}
    texts = [(raw.get(m.external_message_id) if m.external_message_id in raw else m.content_redacted) or "" for m in rows]
    texts = [t.strip() for t in texts if t.strip()]
    if len(texts) <= 1:
        return msg.text or "", 1
    return "\n".join(texts), len(rows)


async def acknowledge_event(maker: async_sessionmaker[AsyncSession], event_id: int) -> None:
    """Seen + typing bubble the moment a message arrives, before the debounce wait. Best effort."""
    async with maker() as session:
        ev = await session.get(WebhookEvent, event_id)
        if ev is None or ev.status == "done":
            return
        msg = IncomingMessage.from_json(ev.payload)
        if msg.external_user_id.startswith(TEST_PREFIX):
            return
        try:
            creds = await get_credentials(session, msg.channel, account_id=msg.recipient_id)
        except ChannelNotConfigured:
            return
        paused = (await session.execute(select(Conversation.ai_paused).join(
            ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id).where(
            ChannelIdentity.channel == msg.channel, ChannelIdentity.external_id == msg.external_user_id,
            Conversation.status != "closed").order_by(Conversation.id.desc()).limit(1))).scalar_one_or_none()
    adapter = get_adapter(msg.channel)
    try:
        await adapter.mark_read(msg.external_user_id, msg.message_id, creds)
        if not paused:  # nobody is about to reply while a person has taken over
            await adapter.show_typing(msg.external_user_id, msg.message_id, creds)
    except Exception as exc:  # noqa: BLE001
        logger.info("early acknowledgement failed on %s: %s", msg.channel, exc)


async def _refresh_profile(adapter, ident, creds) -> None:
    """Name + photo from the channel (Messenger/Instagram). Photo URLs expire, so refresh once a day."""
    now = datetime.now(timezone.utc)
    if ident.profile_fetched_at and now - ident.profile_fetched_at < PROFILE_REFRESH:
        return
    ident.profile_fetched_at = now
    try:
        up = await adapter.fetch_profile(ident.external_id, creds)
    except Exception as exc:  # noqa: BLE001 - e.g. missing permission; the chat still works without it
        logger.info("profile fetch failed on %s: %s", ident.channel, exc)
        return
    if up and up.name:
        ident.display_name = up.name[:160]
    if up and up.avatar_url:
        ident.avatar_url = up.avatar_url


class _Typing:
    """Keeps the typing bubble visible (Meta drops it after ~20 s) until stop()."""

    def __init__(self, adapter, msg: IncomingMessage, creds):
        self._task = asyncio.create_task(self._run(adapter, msg, creds))

    @staticmethod
    async def _run(adapter, msg: IncomingMessage, creds) -> None:
        for _ in range(TYPING_MAX_REFRESHES):
            try:
                await adapter.show_typing(msg.external_user_id, msg.message_id, creds)
            except Exception as exc:  # noqa: BLE001
                logger.info("typing indicator failed on %s: %s", msg.channel, exc)
                return
            await asyncio.sleep(TYPING_REFRESH_S)

    def stop(self) -> None:
        self._task.cancel()


async def _say(session: AsyncSession, r: Resolved, text: str, **kw) -> None:
    flag = None
    if security.asks_for_card(text):  # never, whatever the AI or a canned text says
        logger.warning("reply asking for card details replaced (conversation %s): %.200s", r.conversation.id, text)
        r.events.append("reply asked for card details: replaced, alert raised")
        text, flag = await localized(session, "card_warning", text), security.CARD_REQUEST_BLOCKED
        r.notice = ""
    if r.notice or r.reply_prefix or r.reply_suffix:
        text = "\n\n".join(x for x in (r.notice, r.reply_prefix, text, r.reply_suffix) if x)
        r.notice = r.reply_prefix = r.reply_suffix = ""
    # a long reply goes as several short messages (reply_max_lines each); the last one carries the sources
    parts = split_reply(text, int(await get_setting(session, "reply_max_lines") or 0))
    try:
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            msg = await send_to_conversation(session, r.conversation, part, sender_role="assistant",
                                             **(kw if last else {k: v for k, v in kw.items() if k not in ("sources",)}))
            if flag and not msg.flag:
                msg.flag = flag
        # follow-ups (program district question, per-type follow-up) go as their own short bubbles, after the answer
        followups, r.followups = [f for f in r.followups if f and f.strip()], []
        for extra in followups:
            await send_to_conversation(session, r.conversation, extra, sender_role="assistant", sources={"path": "followup"})
    except (OutOfWindowError, SendFailed) as exc:
        logger.warning("assistant reply not delivered: %s", exc)  # already recorded on the message row
