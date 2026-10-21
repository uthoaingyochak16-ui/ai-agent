"""The Knowledge page's overview and its "search everything" box: one place to see what the assistant knows and to find
a fact wherever it is kept (a document, a Q&A, a value, a program, a data sheet)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.db.session import get_session
from app.models import (Chunk, DataRow, DataSheet, Document, Program, QAPair, SemanticCache, UnansweredQuestion,
                        Variable)
from app.services import sheets as S

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(any_staff)])
PER_KIND = 8


def _snip(text: str | None, q: str, width: int = 160) -> str:
    t = " ".join((text or "").split())
    i = t.lower().find(q.lower())
    if i < 0 or len(t) <= width:
        return t[:width]
    start = max(0, i - width // 3)
    return ("…" if start else "") + t[start:start + width] + ("…" if start + width < len(t) else "")


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    count = lambda q: session.execute(select(func.count()).select_from(q.subquery()))  # noqa: E731
    today = datetime.now(timezone.utc).date()
    docs = select(Document.id).where(Document.status != "superseded")
    return {
        "documents": (await count(docs)).scalar_one(),
        "documents_failed": (await count(docs.where(Document.status == "failed"))).scalar_one(),
        "documents_busy": (await count(docs.where(Document.status.in_(("pending", "processing"))))).scalar_one(),
        "chunks": (await count(select(Chunk.id).join(Document).where(Document.status == "ready"))).scalar_one(),
        "qa": (await count(select(QAPair.id).where(QAPair.active.is_(True)))).scalar_one(),
        "qa_categories": dict((await session.execute(select(QAPair.category, func.count()).where(QAPair.active.is_(True))
                                                     .group_by(QAPair.category))).all()),
        "values": (await count(select(Variable.key))).scalar_one(),
        "values_empty": (await count(select(Variable.key).where(or_(Variable.value.is_(None), func.trim(Variable.value) == "")))).scalar_one(),
        "programs": (await count(select(Program.id).where(func.coalesce(Program.end_date, Program.start_date) >= today))).scalar_one(),
        "unknown": (await count(select(UnansweredQuestion.id).where(UnansweredQuestion.status.in_(("new", "reviewing"))))).scalar_one(),
        "saved_answers": (await count(select(SemanticCache.id))).scalar_one(),
        "sheets": await S.overview(session),
    }


@router.get("/search")
async def search(q: str = Query(min_length=2, max_length=100), session: AsyncSession = Depends(get_session)):
    """Every kind of knowledge that contains these words (all of them), a few of each."""
    words = q.split()[:6]
    like = [f"%{w}%" for w in words]

    def all_of(*cols):
        return [or_(*(c.ilike(x) for c in cols)) for x in like]
    chunks = (await session.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
                                    .where(Document.status == "ready", *all_of(Chunk.content, Chunk.question, Document.title))
                                    .limit(PER_KIND))).all()
    qas = (await session.execute(select(QAPair).where(*all_of(QAPair.question, QAPair.answer_template, QAPair.category))
                                 .order_by(QAPair.active.desc(), QAPair.id.desc()).limit(PER_KIND))).scalars().all()
    values = (await session.execute(select(Variable).where(*all_of(Variable.key, Variable.value, Variable.description))
                                    .limit(PER_KIND))).scalars().all()
    programs = (await session.execute(select(Program).where(*all_of(Program.name, Program.place, Program.notes))
                                      .order_by(Program.start_date.desc()).limit(PER_KIND))).scalars().all()
    rows = (await session.execute(select(DataRow, DataSheet).join(DataSheet, DataRow.sheet_id == DataSheet.id)
                                  .where(*[DataRow.search_text.contains(w.lower()) for w in words]).limit(PER_KIND * 2))).all()
    return {
        "documents": [{"document_id": d.id, "document": d.title, "chunk_id": c.id, "title": c.question,
                       "snippet": _snip(c.content, words[0])} for c, d in chunks],
        "qa": [{"id": x.id, "question": x.question, "snippet": _snip(x.answer_template, words[0]), "category": x.category,
                "active": x.active} for x in qas],
        "values": [{"key": v.key, "value": v.value, "description": v.description} for v in values],
        "programs": [{"id": p.id, "name": p.name, "place": p.place, "start_date": p.start_date} for p in programs],
        "sheets": [{"sheet_id": s.id, "sheet": s.name, "row_id": r.id, "snippet": _snip(S.row_line(s, r.values or {}).split(" — ", 1)[-1], words[0])}
                   for r, s in rows],
    }
