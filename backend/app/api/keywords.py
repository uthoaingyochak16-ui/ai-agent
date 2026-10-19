"""Keyword index admin: size and top keywords, rebuild, a search tester, the keyword table (every record with its
keywords, editable), the uniqueness check, AI keyword generation, a live preview for new input, and the index tree."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.db.session import get_session
from app.llm.service import LLMUnavailable
from app.rag.keyword_index import _load, rebuild_index, reindex_keys, stats
from app.rag.keyword_search import keyword_retrieve
from app.rag.keyword_tools import (TYPES, ai_generate, edit_record, keyword_tree, list_records, look_alike_report, make_unique,
                                   preview)
from app.services.learning import refresh_suggestions
from app.services.reply_engine import get_llm_service
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api/keywords", tags=["keyword index"])


@router.get("/stats", dependencies=[Depends(any_staff)])
async def index_stats(session: AsyncSession = Depends(get_session)):
    return await stats(session)


@router.post("/rebuild", dependencies=[Depends(admin_only)])
async def rebuild(session: AsyncSession = Depends(get_session)):
    """Regenerate every record's keywords from the data (keeps hand-made and AI keywords) and re-run the uniqueness check."""
    counts = await rebuild_index(session)
    # Unknown questions get suggested keywords on their own row (guest wording never enters the index)
    counts["unknown_suggestions"] = await refresh_suggestions(session)
    await session.commit()
    return {"records": counts, **await stats(session)}


@router.get("/search", dependencies=[Depends(admin_only)])
async def search(q: str = Query(min_length=1, max_length=500), ai: bool = False, session: AsyncSession = Depends(get_session)):
    """What the assistant would look up for this question. ai=true also makes the (billed) keyword call."""
    res = await keyword_retrieve(session, get_llm_service(session), text=q, retrieval_text=q, history=[], channel=None,
                                 conversation_id=None, is_test=True, allow_ai=ai)  # free by default: rule-based keywords only
    return {"debug": res.debug, "usable": res.usable, "confidence": res.confidence,
            "records": [{"document": h.document.title, "question": h.chunk.question, "text": (h.text or h.chunk.content)[:400],
                         "coverage": h.weighted} for h in res.hits], "extra": [e[:300] for e in res.extra]}


def _type(t: str) -> str:
    if t not in TYPES:
        raise HTTPException(404, "Unknown record type")
    return t


# ---- the keyword table ------------------------------------------------------------------------------------------------
@router.get("/records", dependencies=[Depends(any_staff)])
async def records(type: str = "qa", search: str | None = None, limit: int = Query(40, le=200), offset: int = 0,
                  session: AsyncSession = Depends(get_session)):
    """Records of one kind with their keywords: where each came from (title, file, body, signature, phrase, ai, manual,
    unique) and how many records share it (1 = only this record)."""
    return await list_records(session, _type(type), search=search, limit=limit, offset=offset)


class EditIn(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=60)     # stems of keywords to switch off
    restore: list[str] = Field(default_factory=list, max_length=60)


@router.put("/records/{st}/{ref}", dependencies=[Depends(admin_only)])
async def edit(st: str, ref: str, body: EditIn, session: AsyncSession = Depends(get_session)):
    st = _type(st)
    await edit_record(session, st, ref, add=[k for k in body.add if k.strip()], remove=body.remove, restore=body.restore)
    await session.commit()
    return {"ok": True}


class GenerateIn(BaseModel):
    ai: bool = False


@router.post("/records/{st}/{ref}/generate", dependencies=[Depends(admin_only)])
async def generate(st: str, ref: str, body: GenerateIn, session: AsyncSession = Depends(get_session)):
    """Regenerate this record's automatic keywords from its text, give it unique keywords where it looks like another
    record, and optionally ask the AI (only grounded suggestions are kept)."""
    st = _type(st)
    if await session.run_sync(lambda s: _load(s.connection(), st, ref)) is None:
        raise HTTPException(404, "Record not found")
    await session.run_sync(lambda s: reindex_keys(s.connection(), {(st, ref)}))
    out = await make_unique(session, st, ref)
    if body.ai:
        try:
            out["ai"] = await ai_generate(session, get_llm_service(session), st, ref)
        except LLMUnavailable as exc:
            await session.rollback()
            raise HTTPException(409, f"Set the “Keyword generation” route in AI Settings → Model routing first ({exc})") from exc
    await session.commit()
    return out


class BulkAiIn(BaseModel):
    types: list[str] = Field(default_factory=lambda: ["qa"])
    limit: int = Field(25, ge=1, le=300)


@router.post("/generate-ai", dependencies=[Depends(admin_only)])
async def generate_ai(body: BulkAiIn, request: Request, session: AsyncSession = Depends(get_session)):
    """AI keywords for records that have none yet. Runs in the background; every record is one small AI call."""
    for t in body.types:
        _type(t)
    from app.services.settings_service import set_setting
    await set_setting(session, "keygen_progress", {"status": "queued", "done": 0, "total": 0})
    await session.commit()
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        raise HTTPException(503, "The background worker is not available")
    await queue.enqueue_job("keygen_job", body.types, body.limit)
    return {"queued": True}


@router.get("/generate-ai/status", dependencies=[Depends(any_staff)])
async def generate_ai_status(session: AsyncSession = Depends(get_session)):
    return await get_setting(session, "keygen_progress") or {"status": "idle", "done": 0, "total": 0}


class PreviewIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field("", max_length=8000)
    category: str | None = Field(None, max_length=64)


@router.post("/preview", dependencies=[Depends(any_staff)])
async def preview_keywords(body: PreviewIn, session: AsyncSession = Depends(get_session)):
    """The keywords a new Q&A would get (free, no AI), and how many existing records already use each."""
    return {"keywords": await preview(session, body.question, body.answer, body.category)}


@router.get("/tree", dependencies=[Depends(any_staff)])
async def tree(roots: int = Query(24, ge=3, le=60), children: int = Query(8, ge=2, le=16), depth: int = Query(3, ge=1, le=4),
               session: AsyncSession = Depends(get_session)):
    return await keyword_tree(session, roots=roots, children=children, depth=depth)


@router.get("/look-alikes", dependencies=[Depends(admin_only)])
async def look_alikes_report(session: AsyncSession = Depends(get_session)):
    return await look_alike_report(session)
