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
from app.services import security
from app.services.outbound import TEST_PREFIX, SendFailed, get_credentials, send_to_conversation

router = APIRouter(prefix="/api/conversations", tags=["conversations"], dependencies=[Depends(any_staff)])


def _msg(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content_redacted, "created_at": m.created_at,
            "delivery_status": m.delivery_status, "flag": m.flag, "attachments": m.attachments,
            "sources": m.sources_json, "tokens_in": m.tokens_in, "tokens_out": m.tokens_out}


def _waiting():
    """The latest message of the conversation is the guest's: nobody (AI or staff) has answered it yet."""
    latest = select(Message.role).where(Message.conversation_id == Conversation.id, Message.role.in_(("user", "assistant", "agent")))         .order_by(Message.id.desc()).limit(1).correlate(Conversation).scalar_subquery()
    return latest == "user"


async def _account_labels(session: AsyncSession) -> dict[tuple[str, str], str]:
    from app.models import ChannelConfig
    rows = (await session.execute(select(ChannelConfig.channel, ChannelConfig.account_id, ChannelConfig.label))).all()
    return {(ch, acc): label for ch, acc, label in rows if acc and label}


@router.get("")
async def list_conversations(channel: str | None = None, status: str | None = None, profile_id: int | None = None, include_test: bool = False,
                             account_id: str | None = None, waiting: bool = False, q: str | None = Query(None, max_length=100),
                             limit: int = Query(50, le=200), offset: int = 0,
                             session: AsyncSession = Depends(get_session)):
    """The unified inbox: every platform and every connected Page / account / number. account_id: one of them only;
    waiting: the guest wrote last; q: the guest's name or words of the chat."""
    query = (select(Conversation, ChannelIdentity, Profile).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
             .outerjoin(Profile, Conversation.profile_id == Profile.id))
    if not include_test:
        query = query.where(~ChannelIdentity.external_id.like("test:%"))
    if channel:
        query = query.where(ChannelIdentity.channel == channel)
    if account_id:
        query = query.where(ChannelIdentity.account_id == account_id)
    if status:
        query = query.where(Conversation.status == status)
    if profile_id:
        query = query.where(Conversation.profile_id == profile_id)
    if waiting:
        query = query.where(_waiting())
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(Profile.name.ilike(like) | ChannelIdentity.display_name.ilike(like)
                            | select(Message.id).where(Message.conversation_id == Conversation.id,
                                                       Message.content_redacted.ilike(like)).correlate(Conversation).exists())
    rows = (await session.execute(query.order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
                                  .limit(limit).offset(offset))).all()
    labels = await _account_labels(session)
    out = []
    for c, i, p in rows:
        last = (await session.execute(select(Message).where(Message.conversation_id == c.id)
                                      .order_by(Message.id.desc()).limit(1))).scalar_one_or_none()
        out.append({"id": c.id, "channel": i.channel, "status": c.status, "ai_paused": c.ai_paused,
                    "profile_id": c.profile_id, "name": (p.name if p and p.name else i.display_name), "avatar_url": i.avatar_url,
                    "last_message": last.content_redacted[:140] if last else None, "last_message_at": c.last_message_at,
                    "last_role": last.role if last else None, "waiting": bool(last and last.role == "user"),
                    "account_id": i.account_id, "account_label": labels.get((i.channel, i.account_id or "")),
                    "last_user_message_at": i.last_user_message_at})
    return out


@router.get("/summary")
async def inbox_summary(session: AsyncSession = Depends(get_session)):
    """Counts for the inbox side bar: per platform, per connected Page / account / number, needing a person, waiting
    for a reply, and Facebook comments waiting for staff."""
    from app.models import ChannelConfig, PostComment
    real = ~ChannelIdentity.external_id.like("test:%")
    base = select(func.count()).select_from(Conversation).join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id).where(real)
    per = dict((await session.execute(select(ChannelIdentity.channel, func.count()).select_from(Conversation)
                                      .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id).where(real)
                                      .group_by(ChannelIdentity.channel))).all())
    per_acc = {(ch, acc): n for ch, acc, n in (await session.execute(
        select(ChannelIdentity.channel, ChannelIdentity.account_id, func.count()).select_from(Conversation)
        .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id).where(real)
        .group_by(ChannelIdentity.channel, ChannelIdentity.account_id))).all()}
    cfgs = (await session.execute(select(ChannelConfig).order_by(ChannelConfig.channel, ChannelConfig.id))).scalars().all()
    accounts = [{"channel": a.channel, "account_id": a.account_id, "label": a.label, "status": a.status, "enabled": a.enabled,
                 "conversations": per_acc.get((a.channel, a.account_id), 0)} for a in cfgs if a.account_id]
    # per platform: is anything connected, and does it work (the Inbox tells the staff what to fix)
    platforms = {ch: {"accounts": sum(1 for a in accounts if a["channel"] == ch),
                      "working": sum(1 for a in accounts if a["channel"] == ch and a["status"] == "ok"),
                      "problem": next((a.status_detail for a in cfgs if a.channel == ch and a.status == "error" and a.status_detail), None)}
                 for ch in ("messenger", "instagram", "whatsapp")}
    return {"all": (await session.execute(base)).scalar_one(),
            "handover": (await session.execute(base.where(Conversation.status == "handover"))).scalar_one(),
            "waiting": (await session.execute(base.where(_waiting()))).scalar_one(),
            "channels": {ch: per.get(ch, 0) for ch in ("messenger", "instagram", "whatsapp")},
            "accounts": accounts, "platforms": platforms,
            "comments_waiting": (await session.execute(select(func.count()).select_from(PostComment).where(
                PostComment.status.in_(("draft", "needs_staff", "failed"))))).scalar_one()}


@router.get("/{conv_id}")
async def get_conversation(conv_id: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Not found")
    i = await session.get(ChannelIdentity, c.channel_identity_id)
    p = await session.get(Profile, c.profile_id) if c.profile_id else None
    msgs = (await session.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.id))).scalars().all()
    from app.models import PostComment
    labels = await _account_labels(session)
    # the same person's comments on the Page's posts (the comment's author id is the one Meta uses for this Page)
    comments = (await session.execute(select(PostComment).where(PostComment.from_id == i.external_id)
                                      .order_by(PostComment.id.desc()).limit(5))).scalars().all() if i.external_id else []
    return {"id": c.id, "channel": i.channel, "status": c.status, "ai_paused": c.ai_paused,
            "name": (p.name if p and p.name else i.display_name), "avatar_url": i.avatar_url,
            "handover_reason": c.handover_reason, "profile_id": c.profile_id,
            "account_id": i.account_id, "account_label": labels.get((i.channel, i.account_id or "")),
            "guest_id": i.external_id, "started_at": msgs[0].created_at if msgs else None,
            "comments": [{"id": x.id, "message": x.message, "post_text": (x.post_text or "")[:160], "status": x.status,
                          "comment_url": f"https://www.facebook.com/{x.comment_id}", "received_at": x.received_at,
                          "channel": x.channel} for x in comments],
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
        await get_adapter(i.channel).show_typing(i.external_id, last_in, await get_credentials(session, i.channel, account_id=i.account_id))
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
    if security.asks_for_card(body.text):
        raise HTTPException(422, "Card numbers, CVV, PIN and OTP are never asked for. Remove that from the message.")
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
