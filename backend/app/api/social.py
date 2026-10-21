"""Social media analysis and tools (services/social.py, ads.py, broadcast.py): audience, sentiment, what guests ask,
program funnel, weekly reports, ad drafts and read-only ad results, WhatsApp broadcasts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.core import crypto
from app.db.session import get_session
from app.models import AdDraft, AdminUser, Broadcast, BroadcastRecipient, SocialReport
from app.services import ads as A
from app.services import broadcast as B
from app.services import social as S
from app.services.reply_engine import get_llm_service
from app.services.settings_service import get_setting, set_setting

router = APIRouter(prefix="/api/social", tags=["social analysis"])


# ---- audience, sentiment, questions, funnel ----------------------------------------------------------------------
@router.get("/audience")
async def get_audience(days: int = 90, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await S.audience(session, max(7, min(days, 730)))


@router.post("/audience/refresh")
async def refresh_audience(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    res = await S.snapshot(session)
    await session.commit()
    return {"accounts": res}


@router.get("/sentiment")
async def get_sentiment(weeks: int = 12, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await S.sentiment(session, max(1, min(weeks, 52)))


@router.get("/questions")
async def get_questions(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return (await get_setting(session, "question_trends")) or {"topics": [], "at": None}


@router.post("/questions/refresh")
async def refresh_questions(days: int = 30, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    res = await S.question_trends(session, get_llm_service(session), max(7, min(days, 180)))
    await session.commit()
    return res


@router.get("/funnel")
async def get_funnel(days: int = 60, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await S.funnel(session, max(7, min(days, 365)))


# ---- weekly reports ----------------------------------------------------------------------------------------------
def _report(r: SocialReport) -> dict:
    return {"id": r.id, "period_start": r.period_start, "period_end": r.period_end, "summary": r.summary, "data": r.data,
            "sent": r.sent, "created_at": r.created_at}


@router.get("/reports")
async def list_reports(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return [_report(r) for r in (await session.execute(select(SocialReport).order_by(SocialReport.id.desc()).limit(26))).scalars()]


@router.post("/reports")
async def make_report(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    r = await S.weekly_report(session, get_llm_service(session))
    await session.commit()
    return _report(r)


# ---- ads (drafts only; results read-only) -----------------------------------------------------------------------
def _ad(a: AdDraft) -> dict:
    return {"id": a.id, "name": a.name, "objective": a.objective, "post_id": a.post_id, "program_id": a.program_id, "text": a.text,
            "audience": a.audience or {}, "daily_budget": float(a.daily_budget) if a.daily_budget is not None else None,
            "days": a.days, "status": a.status, "note": a.note, "created_by": a.created_by, "created_at": a.created_at}


@router.get("/ads")
async def list_ads(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AdDraft).where(AdDraft.status != "archived").order_by(AdDraft.id.desc()).limit(100))).scalars()
    return {"drafts": [_ad(a) for a in rows], "account_id": await get_setting(session, "ads_account_id") or "",
            "has_token": bool(await get_setting(session, "ads_access_token_enc"))}


class AdSuggestIn(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    post_id: int | None = None
    program_id: int | None = None
    daily_budget: float | None = Field(None, ge=0, le=10_000_000)


@router.post("/ads/suggest")
async def suggest_ad(body: AdSuggestIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    a = await A.suggest(session, get_llm_service(session), goal=body.goal, post_id=body.post_id, program_id=body.program_id,
                        daily_budget=body.daily_budget, by=user.email)
    await session.commit()
    return _ad(a)


class AdPatch(BaseModel):
    name: str | None = Field(None, max_length=160)
    objective: str | None = Field(None, pattern="^(awareness|engagement|messages|traffic|leads)$")
    text: str | None = Field(None, max_length=5000)
    audience: dict | None = None
    daily_budget: float | None = Field(None, ge=0, le=10_000_000)
    days: int | None = Field(None, ge=1, le=365)
    status: str | None = Field(None, pattern="^(draft|approved|started|archived)$")
    note: str | None = Field(None, max_length=2000)


@router.put("/ads/{ad_id}")
async def update_ad(ad_id: int, body: AdPatch, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    a = await session.get(AdDraft, ad_id)
    if a is None:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    await session.commit()
    return _ad(a)


@router.get("/ads/results")
async def ad_results(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await A.results(session)


class AdsTokenIn(BaseModel):
    token: str = Field("", max_length=1000)     # empty: forget it


@router.put("/ads-token")
async def save_ads_token(body: AdsTokenIn, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    await set_setting(session, "ads_access_token_enc", crypto.encrypt(body.token.strip(), aad="ads:token") if body.token.strip() else None)
    await session.commit()
    return {"has_token": bool(body.token.strip())}


# ---- WhatsApp broadcasts -----------------------------------------------------------------------------------------
def _bc(b: Broadcast) -> dict:
    return {"id": b.id, "name": b.name, "template": b.template, "language": b.language, "params": b.params or [],
            "preview": b.preview, "audience": b.audience or {}, "status": b.status, "total": b.total, "sent": b.sent,
            "failed": b.failed, "error": b.error, "created_by": b.created_by, "created_at": b.created_at,
            "started_at": b.started_at, "finished_at": b.finished_at}


class Audience(BaseModel):
    account_id: str | None = Field(None, max_length=64)
    active_days: int | None = Field(None, ge=1, le=3650)


@router.post("/broadcasts/audience")
async def audience_size(body: Audience, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return {"count": await B.count_audience(session, body.model_dump(exclude_none=True))}


@router.get("/broadcasts")
async def list_broadcasts(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return [_bc(b) for b in (await session.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(50))).scalars()]


class BroadcastIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_]+$")   # WhatsApp template names
    language: str = Field("bn", min_length=2, max_length=16)
    params: list[str] = Field(default_factory=list, max_length=10)
    preview: str | None = Field(None, max_length=2000)
    audience: Audience = Field(default_factory=Audience)


@router.post("/broadcasts")
async def create_broadcast(body: BroadcastIn, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    b = Broadcast(name=body.name, template=body.template, language=body.language, params=body.params, preview=body.preview,
                  audience=body.audience.model_dump(exclude_none=True), created_by=user.email)
    session.add(b)
    await session.commit()
    return _bc(b)


@router.post("/broadcasts/{bid}/start")
async def start_broadcast(bid: int, request: Request, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    """Send it now to everyone in its audience (admin only)."""
    b = await session.get(Broadcast, bid)
    if b is None:
        raise HTTPException(404, "Not found")
    if b.status != "draft":
        raise HTTPException(409, f"The broadcast is {b.status}")
    n = await B.start(session, b)
    await session.commit()
    queue = getattr(request.app.state, "queue", None)
    if n and queue is not None:
        await queue.enqueue_job("broadcast_job", b.id, _job_id=f"broadcast-{b.id}")
    return _bc(b)


@router.post("/broadcasts/{bid}/cancel")
async def cancel_broadcast(bid: int, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    b = await session.get(Broadcast, bid)
    if b is None:
        raise HTTPException(404, "Not found")
    if b.status in ("draft", "sending"):
        b.status = "cancelled"
        await session.commit()
    return _bc(b)


@router.get("/broadcasts/{bid}")
async def broadcast_detail(bid: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    b = await session.get(Broadcast, bid)
    if b is None:
        raise HTTPException(404, "Not found")
    by = dict((await session.execute(select(BroadcastRecipient.status, func.count()).where(BroadcastRecipient.broadcast_id == bid)
                                     .group_by(BroadcastRecipient.status))).all())
    errors = (await session.execute(select(BroadcastRecipient.error).where(BroadcastRecipient.broadcast_id == bid,
                                                                           BroadcastRecipient.error.is_not(None)).limit(5))).scalars().all()
    return {**_bc(b), "by_status": by, "errors": errors}
