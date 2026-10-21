"""Comments on the Facebook Page's posts (services/comments.py): what the AI replied, drafted or flagged, and staff's
send / edit / dismiss / hide / show again / delete; "Check a comment" (is it real, spam, or against the organisation?)
and "Scan recent posts". The switches (comment_mode ...) are ordinary settings (AI settings API)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.channels import page_comments as pc
from app.channels.graph import GraphError
from app.db.session import get_session
from app.models import AdminUser, PostComment
from app.services import comments as comments_svc
from app.services.outbound import ChannelNotConfigured, get_credentials
from app.services.reply_engine import get_llm_service
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api/comments", tags=["comments"])
STATUSES = ("draft", "needs_staff", "failed", "replied", "liked", "hidden", "deleted", "ignored", "queued", "processing")
EDITABLE = ("draft", "needs_staff", "failed", "ignored", "liked", "hidden")
SETTING_KEYS = ("comment_mode", "comment_auto_like", "comment_reply_to_praise", "comment_spam_action",
                "comment_against_action", "comment_remove_min_confidence", "comment_private_reply",
                "comment_reply_delay_seconds", "comment_max_replies_per_hour")


def _row(c: PostComment, labels: dict | None = None) -> dict:
    page, _, post = c.post_id.partition("_")
    insta = c.channel == "instagram"
    return {"id": c.id, "received_at": c.received_at, "from_name": c.from_name, "message": c.message, "channel": c.channel,
            "page_id": c.page_id, "account_label": (labels or {}).get((comments_svc.chan_of(c), c.page_id)),
            "post_text": (c.post_text or "")[:400],
            "post_url": c.post_url if insta else f"https://www.facebook.com/{page}/posts/{post or page}",
            "comment_url": c.post_url if insta else f"https://www.facebook.com/{c.comment_id}", "status": c.status, "kind": c.kind,
            "reason": c.reason, "confidence": c.confidence, "action": c.action, "source": c.source,
            "reply_text": c.reply_text, "reply_path": c.reply_path, "liked": c.liked, "private_replied": c.private_replied,
            "replied_at": c.replied_at, "error": c.error, "in_thread": bool(c.parent_id and c.parent_id != c.post_id)}


GROUPS = {"todo": ("draft", "needs_staff", "failed"), "done": ("replied", "liked"), "removed": ("hidden", "deleted"),
          "other": ("ignored", "queued", "processing")}


async def _labels(session: AsyncSession) -> dict:
    from app.models import ChannelConfig
    return {(ch, acc): label for ch, acc, label in (await session.execute(
        select(ChannelConfig.channel, ChannelConfig.account_id, ChannelConfig.label))).all() if acc and label}


@router.get("")
async def list_comments(status: str | None = None, group: str | None = None, channel: str | None = None,
                        page_id: str | None = None, kind: str | None = None, q: str | None = None,
                        limit: int = 100, offset: int = 0,
                        _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Comments of every connected Facebook Page and Instagram account. group: todo | done | removed | other."""
    base = select(PostComment)
    if channel in ("facebook", "instagram"):
        base = base.where(PostComment.channel == channel)
    if page_id:
        base = base.where(PostComment.page_id == page_id)
    if kind:
        base = base.where(PostComment.kind == kind)
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.where(PostComment.message.ilike(like) | PostComment.from_name.ilike(like) | PostComment.reply_text.ilike(like))
    filtered = base
    if status in STATUSES:
        filtered = base.where(PostComment.status == status)
    elif group in GROUPS:
        filtered = base.where(PostComment.status.in_(GROUPS[group]))
    rows = (await session.execute(filtered.order_by(PostComment.id.desc()).limit(max(1, min(limit, 300))).offset(max(0, offset)))).scalars().all()
    sub = base.subquery()
    counts = dict((await session.execute(select(sub.c.status, func.count()).group_by(sub.c.status))).all())
    labels = await _labels(session)
    accounts = [{"channel": ch, "page_id": pid, "label": labels.get(("instagram" if ch == "instagram" else "messenger", pid)), "comments": n}
                for ch, pid, n in (await session.execute(select(PostComment.channel, PostComment.page_id, func.count())
                                                         .group_by(PostComment.channel, PostComment.page_id))).all()]
    return {"items": [_row(c, labels) for c in rows], "counts": {s: counts.get(s, 0) for s in STATUSES},
            "groups": {g: sum(counts.get(s, 0) for s in st) for g, st in GROUPS.items()}, "accounts": accounts,
            "settings": {k: await get_setting(session, k) for k in SETTING_KEYS}}


async def _get(session: AsyncSession, comment_id: int) -> PostComment:
    c = await session.get(PostComment, comment_id)
    if c is None:
        raise HTTPException(404, "Comment not found")
    return c


class SendIn(BaseModel):
    text: str | None = Field(None, max_length=2000)   # staff's edited reply; empty = the draft as it is


@router.post("/{comment_id}/send")
async def send(comment_id: int, body: SendIn, _: AdminUser = Depends(any_staff),
               session: AsyncSession = Depends(get_session)):
    """Post the reply (and like, as the settings say) now."""
    c = await _get(session, comment_id)
    if c.reply_comment_id:
        raise HTTPException(409, "Already replied")
    if c.status not in EDITABLE:
        raise HTTPException(409, f"Comment is {c.status}")
    if body.text is not None and body.text.strip() and body.text.strip() != (c.reply_text or ""):
        c.reply_text, c.reply_path = body.text.strip(), "staff"
    if not c.reply_text:
        raise HTTPException(422, "Nothing to send: write a reply")
    if c.kind in (None, "spam", "abuse", "against", "reaction", "other"):
        c.kind = "question"  # staff chose to answer it: treat it as one (no hiding)
    c.action = None
    try:
        creds = await get_credentials(session, comments_svc.chan_of(c), account_id=c.page_id)
    except ChannelNotConfigured as exc:
        raise HTTPException(409, f"This Page cannot send right now (Channels page): {exc}") from None
    try:
        await comments_svc.publish(session, c, creds)
    except GraphError as exc:
        c.status, c.error = "failed", exc.message[:500]
    await session.commit()
    return _row(c)


@router.post("/{comment_id}/redraft")
async def redraft(comment_id: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Let the AI write the reply again (stays a draft)."""
    c = await _get(session, comment_id)
    if c.reply_comment_id:
        raise HTTPException(409, "Already replied")
    history = await comments_svc._thread(session, c) or []
    await comments_svc.plan(session, get_llm_service(session), c, history)
    c.status, c.error = "draft", None
    await session.commit()
    return _row(c)


@router.post("/{comment_id}/dismiss")
async def dismiss(comment_id: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    c = await _get(session, comment_id)
    if c.status in EDITABLE:
        c.status = "ignored"
        await session.commit()
    return _row(c)


async def _creds(session: AsyncSession, c: PostComment):
    try:
        return await get_credentials(session, comments_svc.chan_of(c), account_id=c.page_id)
    except ChannelNotConfigured as exc:
        raise HTTPException(409, f"This Page cannot act right now (Channels page): {exc}") from None


@router.post("/{comment_id}/{action}")
async def moderate(comment_id: int, action: Literal["hide", "unhide", "delete"], _: AdminUser = Depends(any_staff),
                   session: AsyncSession = Depends(get_session)):
    """Hide (can be undone), show a hidden comment again, or delete it for good."""
    c = await _get(session, comment_id)
    if c.status == "deleted":
        raise HTTPException(409, "Already deleted")
    if action == "unhide" and c.status != "hidden":
        raise HTTPException(409, "Not hidden")
    creds = await _creds(session, c)
    try:
        await {"hide": pc.hide, "unhide": pc.unhide, "delete": pc.delete}[action](c.comment_id, creds, ig=comments_svc.ig(c))
    except GraphError as exc:
        raise HTTPException(502, f"Facebook refused: {exc.message}") from None
    c.status, c.action, c.error = {"hide": "hidden", "unhide": "ignored", "delete": "deleted"}[action], None, None
    await session.commit()
    return _row(c)


class CheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    post_text: str | None = Field(None, max_length=3000)


@router.post("/check")
async def check(body: CheckIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Is this comment a real one, spam, or against the organisation? (Nothing is stored or posted.)"""
    got = await comments_svc.classify(session, get_llm_service(session), body.text, body.post_text)
    return {k: got[k] for k in ("kind", "reason", "confidence", "language")}


class ScanIn(BaseModel):
    posts: int = Field(10, ge=1, le=50)
    page_id: str | None = None


@router.post("/scan")
async def scan(body: ScanIn, request: Request, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Check the comments already on the latest posts. They are moderated as the settings say; replies to them are
    only drafted (never posted on their own)."""
    try:
        res = await comments_svc.scan(session, body.page_id, body.posts)
    except ChannelNotConfigured as exc:
        raise HTTPException(409, f"No Facebook Page is connected (Channels page): {exc}") from None
    except GraphError as exc:
        raise HTTPException(502, f"Facebook refused: {exc.message}") from None
    await session.commit()
    queue = getattr(request.app.state, "queue", None)
    for row_id in res.pop("ids"):
        await comments_svc.enqueue(queue, row_id, 0)
    return res
