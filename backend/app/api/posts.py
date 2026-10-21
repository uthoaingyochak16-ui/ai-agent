"""Social posts (services/posts.py): write, schedule and publish posts to the connected Facebook Pages and Instagram
accounts; their performance; what works best (when, which topic, which format); the AI's plan and captions."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.core.config import get_settings
from app.db.session import get_session
from app.models import AdminUser, SocialPost
from app.services import posts as P
from app.services.reply_engine import get_llm_service

router = APIRouter(prefix="/api/posts", tags=["social posts"])
media_router = APIRouter(tags=["social posts"])          # public: Meta fetches the image of a post from here
EDITABLE = ("draft", "pending_approval", "scheduled", "failed")
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_IMAGE = 8 * 1024 * 1024
_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")


def _media_dir() -> Path:
    d = Path(get_settings().upload_dir) / "post-media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _row(p: SocialPost, labels: dict) -> dict:
    return {"id": p.id, "channel": p.channel, "account_id": p.account_id, "account_label": labels.get((p.channel, p.account_id)),
            "source": p.source, "status": p.status, "message": p.message, "link": p.link, "image_url": p.image_url,
            "media_type": p.media_type, "permalink": p.permalink, "topic": p.topic, "ai_written": p.ai_written,
            "metrics": p.metrics or {}, "engagement": P.engagement(p.metrics), "rate": P.rate(p.metrics),
            "scheduled_at": p.scheduled_at, "published_at": p.published_at, "error": p.error, "created_by": p.created_by,
            "suggested_at": p.suggested_at, "note": p.note, "program_id": p.program_id, "ab_group": p.ab_group,
            "ab_variant": p.ab_variant, "history": p.history or []}


async def _labels(session: AsyncSession) -> dict:
    return {(a["channel"], a["account_id"]): a["label"] for a in await P.accounts(session)}


@router.get("/accounts")
async def list_accounts(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await P.accounts(session)


@router.get("")
async def list_posts(status: str | None = None, channel: str | None = None, account_id: str | None = None,
                     source: str | None = None, q: str | None = None, sort: str = "recent", limit: int = 100, offset: int = 0,
                     start: datetime | None = None, end: datetime | None = None,
                     _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """start / end: the calendar's range (published, scheduled or suggested time)."""
    query = select(SocialPost)
    moment = func.coalesce(SocialPost.published_at, SocialPost.scheduled_at, SocialPost.suggested_at)
    if start:
        query = query.where(moment >= start)
    if end:
        query = query.where(moment < end)
    for col, v in ((SocialPost.status, status), (SocialPost.channel, channel), (SocialPost.account_id, account_id), (SocialPost.source, source)):
        if v:
            query = query.where(col == v)
    if q and q.strip():
        query = query.where(SocialPost.message.ilike(f"%{q.strip()}%") | SocialPost.topic.ilike(f"%{q.strip()}%"))
    when = func.coalesce(SocialPost.published_at, SocialPost.scheduled_at, SocialPost.created_at)
    rows = (await session.execute(query.order_by(when.desc()).limit(500 if sort == "best" else max(1, min(limit, 300))).offset(max(0, offset)))).scalars().all()
    if sort == "best":
        rows = sorted(rows, key=lambda p: -P.engagement(p.metrics))[:max(1, min(limit, 300))]
    counts = dict((await session.execute(select(SocialPost.status, func.count()).group_by(SocialPost.status))).all())
    labels = await _labels(session)
    return {"items": [_row(p, labels) for p in rows], "counts": counts}


class Target(BaseModel):
    channel: str = Field(pattern="^(facebook|instagram)$")
    account_id: str = Field(min_length=1, max_length=64)


class PostIn(BaseModel):
    targets: list[Target] = Field(min_length=1, max_length=10)
    message: str = Field("", max_length=63000)
    variants: list[str] = Field(default_factory=list, max_length=3)   # A/B test: more versions of the text (B, C, D)
    link: str | None = Field(None, max_length=2000)
    image_url: str | None = Field(None, max_length=2000)
    schedule_at: datetime | None = None      # None + publish_now False = a draft
    publish_now: bool = False
    ai_written: bool = False


@router.post("")
async def create_posts(body: PostIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """One post per chosen Page / account (the same text everywhere; edit one afterwards if it should differ)."""
    if not body.message.strip() and not body.image_url:
        raise HTTPException(422, "Write something or add an image")
    if any(t.channel == "instagram" for t in body.targets) and not body.image_url:
        raise HTTPException(422, "Instagram posts need an image")
    if body.schedule_at and body.schedule_at.replace(tzinfo=body.schedule_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(422, "The scheduled time is in the past")
    known = {(a["channel"], a["account_id"]) for a in await P.accounts(session)}
    needs_ok = await _needs_approval(session, user)
    texts = [body.message.strip(), *(v.strip() for v in body.variants if v.strip())]
    group = P.new_ab_group() if len(texts) > 1 else None
    when = body.schedule_at.replace(tzinfo=body.schedule_at.tzinfo or timezone.utc) if body.schedule_at else None
    out = []
    for t in body.targets:
        if (t.channel, t.account_id) not in known:
            raise HTTPException(422, f"{t.channel} account {t.account_id} is not connected")
        for i, text in enumerate(texts):
            # A/B: the versions go out one day apart at the same time, so they meet the same audience and hour
            at = when + timedelta(days=i) if when and group else when
            p = SocialPost(channel=t.channel, account_id=t.account_id, source="app", message=text, link=body.link or None,
                           image_url=body.image_url or None, ai_written=body.ai_written, created_by=user.email,
                           ab_group=group, ab_variant="ABCD"[i] if group else None, scheduled_at=at,
                           status="pending_approval" if needs_ok and (at or body.publish_now) else "scheduled" if at else "draft")
            P.log(p, user.email, f"created ({p.status})")
            session.add(p)
            await session.flush()
            if body.publish_now and p.status == "draft" and not (group and i):
                await P.publish(session, p)
                P.log(p, user.email, f"publish now: {p.status}")
            elif body.publish_now and group and i and p.status == "draft":   # later versions: the same time on the next days
                p.status, p.scheduled_at = "scheduled", datetime.now(timezone.utc) + timedelta(days=i)
            out.append(p)
    await session.commit()
    labels = await _labels(session)
    return [_row(p, labels) for p in out]


class PostPatch(BaseModel):
    message: str | None = Field(None, max_length=63000)
    link: str | None = Field(None, max_length=2000)
    image_url: str | None = Field(None, max_length=2000)
    schedule_at: datetime | None = None
    unschedule: bool = False


async def _needs_approval(session: AsyncSession, user: AdminUser) -> bool:
    from app.services.settings_service import get_setting
    return user.role != "admin" and bool(await get_setting(session, "post_approval_required"))


async def _get(session: AsyncSession, post_id: int) -> SocialPost:
    p = await session.get(SocialPost, post_id)
    if p is None:
        raise HTTPException(404, "Post not found")
    return p


@router.put("/{post_id}")
async def update_post(post_id: int, body: PostPatch, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = await _get(session, post_id)
    if p.status not in EDITABLE:
        raise HTTPException(409, "A published post cannot be changed here")
    for k in ("message", "link", "image_url"):
        if getattr(body, k) is not None:
            setattr(p, k, getattr(body, k).strip() or (None if k != "message" else ""))
    if body.unschedule:
        p.status, p.scheduled_at = "draft", None
    elif body.schedule_at:
        when = body.schedule_at.replace(tzinfo=body.schedule_at.tzinfo or timezone.utc)
        if when < datetime.now(timezone.utc):
            raise HTTPException(422, "The scheduled time is in the past")
        p.status, p.scheduled_at, p.error = ("pending_approval" if await _needs_approval(session, user) else "scheduled"), when, None
    P.log(p, user.email, f"edited ({p.status})")
    await session.commit()
    return _row(p, await _labels(session))


@router.post("/{post_id}/publish")
async def publish_now(post_id: int, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = await _get(session, post_id)
    if p.status not in EDITABLE:
        raise HTTPException(409, f"The post is {p.status}")
    if await _needs_approval(session, user):
        p.status = "pending_approval"
        P.log(p, user.email, "asked to publish: waits for an admin")
    else:
        await P.publish(session, p)
        P.log(p, user.email, f"published now: {p.status}")
    await session.commit()
    return _row(p, await _labels(session))


class AdviceIn(BaseModel):
    language: str = Field("bn", pattern="^(bn|en)$")
    days: int = Field(90, ge=7, le=730)


class ReviewIn(BaseModel):
    note: str | None = Field(None, max_length=1000)


@router.post("/{post_id}/approve")
async def approve(post_id: int, body: ReviewIn, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    """An admin approves a waiting post: it is scheduled for its time, or published now when it has none / it passed."""
    p = await _get(session, post_id)
    if p.status != "pending_approval":
        raise HTTPException(409, f"The post is {p.status}")
    if p.scheduled_at and p.scheduled_at > datetime.now(timezone.utc):
        p.status = "scheduled"
    else:
        await P.publish(session, p)
    P.log(p, user.email, f"approved ({p.status})" + (f": {body.note}" if body.note else ""))
    await session.commit()
    return _row(p, await _labels(session))


@router.post("/{post_id}/reject")
async def reject(post_id: int, body: ReviewIn, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    p = await _get(session, post_id)
    if p.status != "pending_approval":
        raise HTTPException(409, f"The post is {p.status}")
    p.status, p.note = "draft", (body.note or p.note)
    P.log(p, user.email, "sent back to draft" + (f": {body.note}" if body.note else ""))
    await session.commit()
    return _row(p, await _labels(session))


@router.post("/auto-plan")
async def make_auto_plan(body: AdviceIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """The AI's 2-week plan as drafts (suggested time + reason). Nothing is published."""
    res = await P.auto_plan(session, get_llm_service(session), language=body.language)
    await session.commit()
    return res


@router.post("/program-drafts")
async def make_program_drafts(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """An invitation and a reminder draft for each upcoming program that has no post yet."""
    from app.services.settings_service import get_setting
    if not await get_setting(session, "auto_program_drafts"):
        raise HTTPException(409, "Program drafts are switched off (auto_program_drafts)")
    n = await P.program_drafts(session, get_llm_service(session))
    await session.commit()
    return {"drafts": n}


@router.delete("/{post_id}")
async def delete_post(post_id: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Drafts, scheduled and failed posts only (a published post stays on the platform; delete it there)."""
    p = await _get(session, post_id)
    if p.status not in EDITABLE:
        raise HTTPException(409, "A published post is deleted on Facebook / Instagram itself")
    await session.delete(p)
    await session.commit()
    return {"deleted": post_id}


@router.post("/sync")
async def sync_posts(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    res = await P.sync(session, get_llm_service(session))
    await session.commit()
    return res


@router.get("/analytics")
async def get_analytics(days: int = 90, channel: str | None = None, account_id: str | None = None,
                        _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await P.analytics(session, days=max(7, min(days, 730)), channel=channel, account_id=account_id)


@router.post("/advice")
async def get_advice(body: AdviceIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await P.advice(session, get_llm_service(session), language=body.language, days=body.days)


class DraftIn(BaseModel):
    brief: str = Field(min_length=3, max_length=2000)
    platform: str = Field("facebook", pattern="^(facebook|instagram)$")
    language: str = Field("bn", pattern="^(bn|en)$")
    program_id: int | None = None
    count: int = Field(3, ge=1, le=5)
    format: str = Field("post", pattern="^(post|reel|story)$")


@router.post("/draft")
async def write_with_ai(body: DraftIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await P.draft(session, get_llm_service(session), brief=body.brief, platform=body.platform, language=body.language,
                         program_id=body.program_id, count=body.count, format=body.format)


@router.post("/upload")
async def upload_image(file: UploadFile = File(...), _: AdminUser = Depends(any_staff)):
    """An image for a post. Meta fetches it from a public link, so the server's public address must be set
    (PUBLIC_BASE_URL)."""
    ext = IMAGE_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(422, "Use a JPG, PNG or WebP image")
    data = await file.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "The image is larger than 8 MB")
    name = f"{uuid.uuid4().hex}.{ext}"
    (_media_dir() / name).write_bytes(data)
    base = get_settings().public_base_url.rstrip("/")
    return {"url": f"{base}/api/media/{name}", "local": base.startswith("http://localhost") or base.startswith("http://127.")}


@media_router.get("/api/media/{name}")
async def media(name: str):
    if not _NAME.match(name):
        raise HTTPException(404, "Not found")
    path = _media_dir() / name
    if not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path, headers={"cache-control": "public, max-age=86400"})
