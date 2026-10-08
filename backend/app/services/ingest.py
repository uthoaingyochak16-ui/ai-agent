"""Worker-side processing of one normalized incoming message."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.base import IncomingMessage, OutOfWindowError
from app.channels.registry import get_adapter
from app.models import Message, WebhookEvent
from app.privacy.redaction import redact_for_storage
from app.services import consent as consent_svc
from app.services.handover import detect_handover, maybe_auto_resume, pause_ai
from app.services.identity import Resolved, handle_shared_contacts, link_same_person, resolve
from app.services.greeting import greeting_reply
from app.services.smalltalk import smalltalk_reply
from app.services.outbound import (TEST_PREFIX, ChannelNotConfigured, SendFailed, get_credentials,
                                   send_to_conversation)
from app.services.post_reply import run_post_reply
from app.services.reply_engine import generate_reply, get_llm_service
from app.services.language import localized
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
PROFILE_REFRESH = timedelta(hours=24)
TYPING_REFRESH_S = 15
TYPING_MAX_REFRESHES = 8  # ~2 min, the job timeout


async def process_event(maker: async_sessionmaker[AsyncSession], event_id: int) -> str:
    """Returns the final status. Never raises: failures are recorded and retried by the requeue cron."""
    async with maker() as session:
        ev = (await session.execute(select(WebhookEvent).where(WebhookEvent.id == event_id)
                                    .with_for_update(skip_locked=True))).scalar_one_or_none()
        if ev is None or ev.status in ("done", "processing"):
            return "skipped"
        ev.status, ev.attempts = "processing", ev.attempts + 1
        await session.commit()
        try:
            await handle_message(session, IncomingMessage.from_json(ev.payload))
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


async def handle_message(session: AsyncSession, msg: IncomingMessage, llm=None) -> Resolved | None:
    adapter = get_adapter(msg.channel)
    r = await resolve(session, msg)

    already = (await session.execute(select(Message.id).where(
        Message.conversation_id == r.conversation.id, Message.role == "user",
        Message.external_message_id == msg.message_id))).first()
    if already:  # a retry after a crash between "store" and "done"
        return r

    user_msg = Message(conversation_id=r.conversation.id, role="user", content_redacted=redact_for_storage(msg.text),
                       attachments=msg.attachments or None, external_message_id=msg.message_id)
    session.add(user_msg)
    r.conversation.last_message_at = datetime.now(timezone.utc)
    await session.flush()

    creds = None
    if not r.identity.external_id.startswith(TEST_PREFIX):
        try:
            creds = await get_credentials(session, msg.channel)
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

    # typing bubble while we work on the answer; not when a person has taken over (nobody is replying yet)
    typing = _Typing(adapter, msg, creds) if creds is not None and not r.conversation.ai_paused else None
    try:
        ai_replied = await _respond(session, r, msg, llm)
    finally:
        if typing:
            typing.stop()

    # background-ish work after the user already has their answer; failures never affect the conversation
    if ai_replied:
        try:
            r.post = await run_post_reply(session, llm or get_llm_service(session), r.profile, r.conversation, msg.channel,
                                           is_test=r.identity.external_id.startswith(TEST_PREFIX))
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
            logger.exception("post-reply tasks failed")
    return r


async def _respond(session: AsyncSession, r: Resolved, msg: IncomingMessage, llm) -> bool:
    """Steps 1-5. Returns True when the AI answered (post-reply extraction should run)."""
    # 1) "delete my data"
    if msg.text and consent_svc.is_erasure_request(msg.text):
        await consent_svc.erase_profile(session, r.profile.id)
        await session.commit()
        await _say(session, r, await localized(session, "erasure_ack", msg.text))
        await session.commit()
        return False

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
            await _say(session, r, await localized(session, "handover_ack", msg.text))
            await session.commit()
            return False
    if r.conversation.ai_paused:
        # A human still owns the conversation, but deterministic answers from saved Q&A/cache or a
        # strongly matching knowledge excerpt remain useful. Unknown questions stay silent for staff.
        reply = await generate_reply(session, r, msg.text or "", llm=llm, knowledge_only=True)
        r.debug = reply.debug
        if reply.path in ("qa_shortcut", "cache", "kb_extract"):
            r.events.append(f"trusted answer during handover: {reply.path}")
            await _say(session, r, reply.text, sources=reply.sources)
            await session.commit()
        return False

    # 5a) bare greeting -> canned welcome (no AI call)
    greet = await greeting_reply(session, r, msg.text or "")
    if greet:
        r.events.append("greeting reply")
        await _say(session, r, greet, sources={"path": "greeting"})
        await session.commit()
        return False

    # 5b) everyday small talk ("who are you?", "kemon achen") -> ready answer (no AI call)
    chat = await smalltalk_reply(session, msg.text or "")
    if chat:
        r.events.append("small talk reply")
        await _say(session, r, chat, sources={"path": "smalltalk"})
        await session.commit()
        return False

    # 5c) AI reply
    reply = await generate_reply(session, r, msg.text or "", llm=llm)
    r.debug = reply.debug
    await _say(session, r, reply.text, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, sources=reply.sources)
    await session.commit()
    return True


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
    try:
        await send_to_conversation(session, r.conversation, text, sender_role="assistant", **kw)
    except (OutOfWindowError, SendFailed) as exc:
        logger.warning("assistant reply not delivered: %s", exc)  # already recorded on the message row
