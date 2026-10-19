"""Answer cache admin: list / edit / verify / block / delete cached AI answers, and wrong-answer feedback."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.db.session import get_session
from app.models import SemanticCache
from app.rag.documents import bump_context_version
from app.services.answer_cache import STATUSES, block_answer
from app.services.learning import record_unknown
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api/cache", tags=["answer cache"])


def _row(c: SemanticCache, ctx_version: int) -> dict:
    return {"id": c.id, "question": c.question, "answer": c.answer, "status": c.status, "hits": c.hits,
            "created_at": c.created_at, "updated_at": c.updated_at,
            "stale": c.status == "auto" and c.context_version != ctx_version}  # knowledge changed since: not served


@router.get("", dependencies=[Depends(any_staff)])
async def list_cache(search: str | None = None, status: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
                     session: AsyncSession = Depends(get_session)):
    q = select(SemanticCache)
    if search and search.strip():
        q = q.where(or_(SemanticCache.question.ilike(f"%{search.strip()}%"), SemanticCache.answer.ilike(f"%{search.strip()}%")))
    if status in STATUSES:
        q = q.where(SemanticCache.status == status)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(SemanticCache.hits.desc(), SemanticCache.id.desc()).limit(limit).offset(offset))).scalars().all()
    ctx = int(await get_setting(session, "context_version") or 1)
    return {"total": total, "items": [_row(c, ctx) for c in rows]}


class CacheIn(BaseModel):
    answer: str | None = Field(None, min_length=1, max_length=4000)
    status: str | None = None


@router.put("/{cid}", dependencies=[Depends(admin_only)])
async def edit_cache(cid: int, body: CacheIn, session: AsyncSession = Depends(get_session)):
    c = await session.get(SemanticCache, cid)
    if not c:
        raise HTTPException(404, "Not found")
    if body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(422, "status must be auto, verified or blocked")
        c.status = body.status
    if body.answer is not None:
        c.answer = body.answer.strip()
        if body.status is None:
            c.status = "verified"  # an admin wrote it: treat as approved
    c.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _row(c, int(await get_setting(session, "context_version") or 1))


@router.delete("/{cid}", dependencies=[Depends(admin_only)])
async def delete_cache(cid: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(SemanticCache, cid)
    if c:
        await session.delete(c)
        await session.commit()
    return {"ok": True}


class FeedbackIn(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)
    question: str | None = Field(None, max_length=2000)  # the guest message it answered: queued for a corrected draft
    clear_all: bool = False


@router.post("/feedback", dependencies=[Depends(any_staff)])
async def wrong_answer(body: FeedbackIn, session: AsyncSession = Depends(get_session)):
    """An admin marked a reply wrong (Test chat / Inbox): block its cached copies; optionally reset the whole cache."""
    blocked = await block_answer(session, body.answer)
    if body.question and body.question.strip():  # Unknown questions: an improved draft is prepared, never auto-published
        await record_unknown(session, body.question, reason="wrong_answer", wrong_answer=body.answer)
    if body.clear_all:
        await bump_context_version(session)  # every "auto" entry stops being served; verified ones stay
    await session.commit()
    return {"blocked": blocked, "cleared_all": body.clear_all}


@router.post("/clear", dependencies=[Depends(admin_only)])
async def clear_all(session: AsyncSession = Depends(get_session)):
    await bump_context_version(session)
    await session.commit()
    return {"ok": True}
