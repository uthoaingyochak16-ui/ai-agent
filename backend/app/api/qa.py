from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.db.session import get_session
from app.llm.providers import LLMError
from app.models import QAPair, UnansweredQuestion, Variable
from app.rag.documents import bump_context_version
from app.rag.embeddings import embed_texts
from app.rag.extract import _pick
from app.rag.templates import normalize_question
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api", tags=["qa"], dependencies=[Depends(any_staff)])


class QAIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer_template: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(None, max_length=64)
    active: bool = True
    resolve_unanswered_id: int | None = None   # "answer is wrong"/unanswered -> corrected answer


def _row(q: QAPair) -> dict:
    return {"id": q.id, "question": q.question, "answer_template": q.answer_template, "category": q.category,
            "active": q.active, "updated_at": q.updated_at}


@router.get("/unanswered")
async def list_unanswered(limit: int = 100, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(UnansweredQuestion).where(UnansweredQuestion.resolved.is_(False))
                                  .order_by(UnansweredQuestion.id.desc()).limit(min(limit, 500)))).scalars().all()
    return [{"id": u.id, "question": u.question, "channel": u.channel, "reason": u.reason,
             "conversation_id": u.conversation_id, "created_at": u.created_at} for u in rows]


async def _embed(session, texts):
    try:
        return await embed_texts(session, texts)
    except LLMError as exc:
        raise HTTPException(502, f"Embedding failed: {exc}") from exc


@router.get("/qa")
async def list_qa(search: str | None = None, category: str | None = None, limit: int = 100, offset: int = 0,
                  session: AsyncSession = Depends(get_session)):
    q = select(QAPair)
    if search:
        q = q.where(or_(QAPair.question.ilike(f"%{search}%"), QAPair.answer_template.ilike(f"%{search}%")))
    if category:
        q = q.where(QAPair.category == category)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(QAPair.id.desc()).limit(min(limit, 500)).offset(offset))).scalars().all()
    cats = (await session.execute(select(QAPair.category).where(QAPair.category.is_not(None)).distinct().order_by(QAPair.category))).scalars().all()
    return {"total": total, "items": [_row(r) for r in rows], "categories": cats}


@router.post("/qa")
async def create_qa(body: QAIn, session: AsyncSession = Depends(get_session)):
    vec = (await _embed(session, [body.question]))[0]
    qa = QAPair(question=body.question.strip(), question_norm=normalize_question(body.question), answer_template=body.answer_template,
                category=(body.category or None), active=body.active, embedding=vec)
    session.add(qa)
    if body.resolve_unanswered_id:
        u = await session.get(UnansweredQuestion, body.resolve_unanswered_id)
        if u:
            u.resolved = True
    await bump_context_version(session)
    await session.commit()
    await session.refresh(qa)
    return _row(qa)


@router.put("/qa/{qid}")
async def update_qa(qid: int, body: QAIn, session: AsyncSession = Depends(get_session)):
    qa = await session.get(QAPair, qid)
    if not qa:
        raise HTTPException(404, "Not found")
    if body.question.strip() != qa.question:
        qa.embedding = (await _embed(session, [body.question]))[0]
    qa.question, qa.question_norm = body.question.strip(), normalize_question(body.question)
    qa.answer_template, qa.category, qa.active = body.answer_template, body.category or None, body.active
    await bump_context_version(session)
    await session.commit()
    await session.refresh(qa)
    return _row(qa)


@router.delete("/qa/{qid}")
async def delete_qa(qid: int, session: AsyncSession = Depends(get_session)):
    qa = await session.get(QAPair, qid)
    if not qa:
        raise HTTPException(404, "Not found")
    await session.delete(qa)
    await bump_context_version(session)
    await session.commit()
    return {"ok": True}


def _safe_cell(v: str) -> str:
    """Neutralize spreadsheet formula injection in exported CSV."""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/qa-export")
async def export_qa(session: AsyncSession = Depends(get_session)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["question", "answer_template", "category", "active"])
    for q in (await session.execute(select(QAPair).order_by(QAPair.id))).scalars():
        w.writerow([_safe_cell(q.question), _safe_cell(q.answer_template), _safe_cell(q.category or ""), str(q.active).lower()])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": 'attachment; filename="qa_pairs.csv"'})


def _read_records(data: bytes, ext: str) -> list[dict]:
    """Rows of a Q&A file: CSV/TSV, JSON (list of objects, {"items": [...]}, or JSON lines) or Excel."""
    import json
    if ext in ("xlsx", "xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        head = [str(h or "").strip() for h in (rows[0] if rows else [])]
        return [dict(zip(head, r)) for r in rows[1:] if any(v not in (None, "") for v in r)]
    text = data.decode("utf-8-sig", errors="replace")
    if ext in ("json", "jsonl") or text.lstrip()[:1] in ("[", "{"):
        try:
            d = json.loads(text)
        except ValueError:
            try:
                d = [json.loads(line) for line in text.splitlines() if line.strip()]
            except ValueError:
                raise HTTPException(422, "Could not read the JSON file") from None
        if isinstance(d, dict):
            d = next((v for v in d.values() if isinstance(v, list)), [d])
        return [x for x in d if isinstance(x, dict)]
    delim = "\t" if ext == "tsv" else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


@router.post("/qa-import")
async def import_qa(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    data = await file.read(10 * 1024 * 1024)
    records = _read_records(data, (file.filename or "").rsplit(".", 1)[-1].lower())
    keys = list({k for r in records for k in r})
    cols = {c.strip().lower(): c for c in keys}
    qk = _pick(keys, ("question", "questions", "q", "query", "prosno", "proshno", "প্রশ্ন"))
    ak = cols.get("answer_template") or _pick(keys, ("answer", "answers", "a", "reply", "response", "uttor", "উত্তর"))
    if not qk or not ak:
        raise HTTPException(422, "The file needs a question column/key and an answer column/key "
                                 "(e.g. 'question' and 'answer'). CSV, JSON and Excel are supported.")
    rows = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in records]
    rows = [r for r in rows if r.get(qk, "").strip() and r.get(ak, "").strip()][:2000]
    existing = {q.question_norm: q for q in (await session.execute(select(QAPair))).scalars()}
    vecs: list = []
    for i in range(0, len(rows), 64):  # embedding APIs cap the batch size
        vecs += await _embed(session, [r[qk].strip() for r in rows[i:i + 64]])
    catk = cols.get("category") or cols.get("topic")
    created = updated = 0
    for r, vec in zip(rows, vecs):
        norm = normalize_question(r[qk])
        active = (r.get(cols.get("active", "")) or "true").strip().lower() not in ("false", "0", "no")
        cat = ((r.get(catk) or "").strip()[:64] or None) if catk else None
        if norm in existing:
            q = existing[norm]
            q.answer_template, q.category, q.active = r[ak].strip(), cat, active
            updated += 1
        else:
            q = QAPair(question=r[qk].strip(), question_norm=norm, answer_template=r[ak].strip(), category=cat, active=active, embedding=vec)
            session.add(q)
            existing[norm] = q
            created += 1
    await bump_context_version(session)
    await session.commit()
    return {"created": created, "updated": updated, "skipped": 0}


class VarIn(BaseModel):
    value: str = Field(max_length=2000)
    description: str | None = Field(None, max_length=300)  # how guests ask for it; None = keep the current one


@router.get("/variables")
async def list_vars(session: AsyncSession = Depends(get_session)):
    return [{"key": v.key, "value": v.value, "description": v.description}
            for v in (await session.execute(select(Variable).order_by(Variable.key))).scalars()]


@router.put("/variables/{key}")
async def put_var(key: str, body: VarIn, session: AsyncSession = Depends(get_session)):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key):
        raise HTTPException(422, "Key may contain letters, digits and underscores only")
    v = await session.get(Variable, key)
    if v is None:
        v = Variable(key=key, value=body.value)
        session.add(v)
    v.value = body.value
    if body.description is not None:
        v.description = body.description.strip() or None
    await bump_context_version(session)
    await session.commit()
    return {"key": key, "value": v.value, "description": v.description}


@router.delete("/variables/{key}")
async def del_var(key: str, session: AsyncSession = Depends(get_session)):
    v = await session.get(Variable, key)
    if v:
        await session.delete(v)
        await bump_context_version(session)
        await session.commit()
    return {"ok": True}
