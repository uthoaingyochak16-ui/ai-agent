from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.channels.base import OutOfWindowError, TemplateMessage
from app.db.session import get_session
from app.models import AdminUser, ChannelIdentity, Conversation, Message, Profile
from app.services.handover import pause_ai, resume_ai
from app.channels.registry import get_adapter
from app.services.outbound import TEST_PREFIX, SendFailed, get_credentials, send_to_conversation

router = APIRouter(prefix="/api/conversations", tags=["conversations"], dependencies=[Depends(any_staff)])


def _msg(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content_redacted, "created_at": m.created_at,
            "delivery_status": m.delivery_status, "flag": m.flag, "attachments": m.attachments,
            "sources": m.sources_json, "tokens_in": m.tokens_in, "tokens_out": m.tokens_out}


@router.get("")
async def list_conversations(channel: str | None = None, status: str | None = None, profile_id: int | None = None, include_test: bool = False,
                             limit: int = Query(50, le=200), offset: int = 0,
                             session: AsyncSession = Depends(get_session)):
    q = (select(Conversation, ChannelIdentity, Profile).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
         .outerjoin(Profile, Conversation.profile_id == Profile.id))
    if not include_test:
        q = q.where(~ChannelIdentity.external_id.like("test:%"))
    if channel:
        q = q.where(ChannelIdentity.channel == channel)
    if status:
        q = q.where(Conversation.status == status)
    if profile_id:
        q = q.where(Conversation.profile_id == profile_id)
    rows = (await session.execute(q.order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
                                  .limit(limit).offset(offset))).all()
    out = []
    for c, i, p in rows:
        last = (await session.execute(select(Message).where(Message.conversation_id == c.id)
                                      .order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
        out.append({"id": c.id, "channel": i.channel, "status": c.status, "ai_paused": c.ai_paused,
                    "profile_id": c.profile_id, "name": (p.name if p and p.name else i.display_name), "avatar_url": i.avatar_url,
                    "last_message": last.content_redacted[:140] if last else None, "last_message_at": c.last_message_at,
                    "last_user_message_at": i.last_user_message_at})
    return out


@router.get("/{conv_id}")
async def get_conversation(conv_id: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    i = await session.get(ChannelIdentity, c.channel_identity_id)
    p = await session.get(Profile, c.profile_id) if c.profile_id else None
    msgs = (await session.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.id))).scalars().all()
    return {"id": c.id, "channel": i.channel, "status": c.status, "ai_paused": c.ai_paused,
            "name": (p.name if p and p.name else i.display_name), "avatar_url": i.avatar_url,
            "handover_reason": c.handover_reason, "profile_id": c.profile_id,
            "last_user_message_at": i.last_user_message_at, "messages": [_msg(m) for m in msgs]}


@router.post("/{conv_id}/pause")
async def pause(conv_id: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    await pause_ai(session, c, "manual")
    await session.commit()
    return {"ok": True, "ai_paused": True}


@router.post("/{conv_id}/resume")
async def resume(conv_id: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    await resume_ai(session, c)
    await session.commit()
    return {"ok": True, "ai_paused": False}


@router.post("/{conv_id}/typing")
async def typing(conv_id: int, session: AsyncSession = Depends(get_session)):
    """Staff is writing a reply: show the typing bubble to the customer. Best effort, never errors."""
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    i = await session.get(ChannelIdentity, c.channel_identity_id)
    if i.external_id.startswith(TEST_PREFIX):
        return {"ok": False}
    last_in = (await session.execute(select(Message.external_message_id).where(
        Message.conversation_id == c.id, Message.role == "user").order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
    try:
        await get_adapter(i.channel).show_typing(i.external_id, last_in, await get_credentials(session, i.channel))
    except Exception:  # noqa: BLE001 - channel off, window closed, missing permission...
        return {"ok": False}
    return {"ok": True}


class TemplateIn(BaseModel):
    name: str
    language: str = "en"
    body_params: list[str] = []


class ReplyIn(BaseModel):
    text: str = Field("", max_length=4000)
    template: TemplateIn | None = None


@router.post("/{conv_id}/reply")
async def reply(conv_id: int, body: ReplyIn, user: AdminUser = Depends(any_staff),
                session: AsyncSession = Depends(get_session)):
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    if not body.text.strip() and not body.template:
        raise HTTPException(422, "text or template required")
    if not c.ai_paused:  # a human taking over implies pausing the AI
        await pause_ai(session, c, f"agent:{user.email}")
    tpl = TemplateMessage(**body.template.model_dump()) if body.template else None
    try:
        msg = await send_to_conversation(session, c, body.text, sender_role="agent", template=tpl)
    except OutOfWindowError as exc:
        await session.commit()  # keep the 'blocked' record
        raise HTTPException(409, {"error": "out_of_window", "needs_template": exc.needs_template}) from exc
    except SendFailed as exc:
        await session.commit()
        raise HTTPException(502, str(exc)) from exc
    await session.commit()
    return _msg(msg)
