"""Test Chat: the real ingest path with a simulated channel, optionally inside a transaction that is rolled back."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import any_staff
from app.channels.base import IncomingMessage
from app.db.session import get_session
from app.llm.service import LLMService
from app.models import AdminUser, ChannelIdentity, Conversation, Message, Profile
from app.privacy.summary import PlainFormatter, compose_summary, missing_fields
from app.services import consent as C
from app.services import inspector
from app.services.reply_style import classify
from app.services.identity import resolve
from app.services.ingest import handle_message
from app.services.outbound import TEST_PREFIX
from app.services.prompt_context import load_profile_view

router = APIRouter(prefix="/api/test-chat", tags=["test chat"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class TestChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    channel: Literal["messenger", "instagram", "whatsapp"] = "messenger"
    session_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    profile_id: int | None = None
    no_save: bool = False
    history: list[Turn] = []      # used when no_save: earlier turns of this simulated chat (nothing persists between calls)


async def _run(s: AsyncSession, body: TestChatIn, llm: LLMService) -> dict:
    sid = body.session_id or uuid.uuid4().hex[:16]
    ext = f"{TEST_PREFIX}{sid}"
    if body.profile_id is not None and await s.get(Profile, body.profile_id) is None:
        raise HTTPException(404, "Profile not found")
    ident = (await s.execute(select(ChannelIdentity).where(ChannelIdentity.channel == body.channel, ChannelIdentity.external_id == ext))).scalar_one_or_none()
    if ident is None:
        ident = ChannelIdentity(channel=body.channel, external_id=ext, display_name="Test chat", profile_id=body.profile_id)
        s.add(ident)
        await s.flush()
    now = datetime.now(timezone.utc)
    if body.no_save and body.history:  # rebuild the conversation so far inside this (rolled-back) transaction
        r0 = await resolve(s, IncomingMessage(channel=body.channel, external_user_id=ext, text="", attachments=[], timestamp=now, message_id=f"seed-{sid}"))
        for t in body.history[-20:]:
            s.add(Message(conversation_id=r0.conversation.id, role=t.role, content_redacted=t.content))
        await s.flush()
    last_id = (await s.execute(select(Message.id).order_by(Message.id.desc()).limit(1))).scalar_one_or_none() or 0
    msg = IncomingMessage(channel=body.channel, external_user_id=ext, text=body.message, attachments=[], timestamp=now,
                          message_id=f"t-{uuid.uuid4().hex}")
    r = await handle_message(s, msg, llm=llm)
    new = (await s.execute(select(Message).where(Message.conversation_id == r.conversation.id, Message.id > last_id).order_by(Message.id))).scalars().all()
    view = await load_profile_view(s, r.profile)
    prof = {"id": r.profile.id, "summary": compose_summary(view, PlainFormatter()), "missing_fields": list(missing_fields(view)),
            "consent": C.consent_state(r.profile), "pending_fields": sorted(view.pending)}
    debug = dict(r.debug or {})
    debug.setdefault("path", "handover" if r.conversation.ai_paused else "none")
    debug["events"], debug["post_reply"] = r.events, r.post
    if "inspector" not in debug:  # canned, lead, handover, district... replies: inspect them the same way
        replies = [m.content_redacted for m in new if m.role == "assistant"]
        main = next((m.content_redacted for m in new if m.role == "assistant" and (m.sources_json or {}).get("path") != "followup"), "")
        follow = [m.content_redacted for m in new if m.role == "assistant" and (m.sources_json or {}).get("path") == "followup"]
        facts = [x for x in prof["summary"].split("; ") if x] + (["phone: on file"] if view.has_phone else [])
        debug["inspector"] = inspector.build(question=body.message, debug=debug, reply=main if replies else "",
                                             qtype=r.qtype or classify(body.message, debug.get("path"), debug),
                                             profile_facts=facts, followups=follow, paused=r.conversation.ai_paused)
    return {"session_id": sid, "conversation_id": None if body.no_save else r.conversation.id, "ai_paused": r.conversation.ai_paused,
            "handover_reason": r.conversation.handover_reason,
            "messages": [{"role": m.role, "content": m.content_redacted if m.role != "assistant" else m.content_redacted,
                          "delivery_status": m.delivery_status, "sources": m.sources_json} for m in new],
            "profile": prof, "debug": debug, "saved": not body.no_save}


@router.post("")
async def test_chat(body: TestChatIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    engine = session.bind
    llm = LLMService(async_sessionmaker(engine, expire_on_commit=False))   # cost rows commit on their own connection
    if not body.no_save:
        out = await _run(session, body, llm)
        await session.commit()
        return out
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            return await _run(s, body, llm)
        finally:
            await s.close()
            await trans.rollback()
