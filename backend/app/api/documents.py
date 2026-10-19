from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.db.session import get_session
from app.models import Chunk, Document
from app.rag.documents import bump_context_version, get_storage
from app.rag.embeddings import embed_texts, embedding_signature
from app.rag.extract import MEDIA_TYPES, sniff

router = APIRouter(prefix="/api/documents", tags=["knowledge base"])
MAX_BYTES = 50 * 1024 * 1024


def _row(d: Document) -> dict:
    return {"id": d.id, "title": d.title, "type": d.type, "version": d.version, "status": d.status, "error": d.error,
            "chunk_count": d.chunk_count, "created_at": d.created_at}


async def _enqueue(request: Request, doc_id: int) -> None:
    q = getattr(request.app.state, "queue", None)
    if q is not None:
        await q.enqueue_job("process_document_job", doc_id)


@router.get("", dependencies=[Depends(any_staff)])
async def list_documents(session: AsyncSession = Depends(get_session)):
    return [_row(d) for d in (await session.execute(select(Document).order_by(Document.id.desc()))).scalars()]


@router.post("", dependencies=[Depends(admin_only)])
async def upload(request: Request, file: UploadFile = File(...), title: str | None = Form(None),
                 session: AsyncSession = Depends(get_session)):
    name = file.filename or "document"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in MEDIA_TYPES:  # every other format is accepted; extraction reports what it could not read
        raise HTTPException(415, "Audio/video can't be read as text. Upload a transcript instead.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (50 MB max)")
    if not data:
        raise HTTPException(422, "Empty file")
    if not ext or len(ext) > 8 or not ext.isalnum():
        ext = sniff(data)  # no / odd extension: name the type from the content
    t = (title or name.rsplit(".", 1)[0]).strip()[:255]
    version = ((await session.execute(select(func.max(Document.version)).where(Document.title == t))).scalar_one() or 0) + 1
    doc = Document(title=t, file_path=get_storage().save(name, data), type=ext, version=version, status="pending")
    session.add(doc)
    await session.commit()
    await _enqueue(request, doc.id)
    return _row(doc)


@router.post("/reprocess-all", dependencies=[Depends(admin_only)])
async def reprocess_all(request: Request, session: AsyncSession = Depends(get_session)):
    """After changing the chunk size: cut every current document again."""
    docs = (await session.execute(select(Document).where(Document.status.in_(("ready", "failed"))))).scalars().all()
    for d in docs:
        d.status, d.error = "pending", None
    await session.commit()
    for d in docs:
        await _enqueue(request, d.id)
    return {"queued": len(docs)}


@router.post("/{doc_id}/reprocess", dependencies=[Depends(admin_only)])
async def reprocess(doc_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    d = await session.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    d.status, d.error = "pending", None
    await session.commit()
    await _enqueue(request, d.id)
    return _row(d)


@router.get("/{doc_id}/chunks", dependencies=[Depends(any_staff)])
async def chunks(doc_id: int, limit: int = 50, offset: int = 0, session: AsyncSession = Depends(get_session)):
    d = await session.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    rows = (await session.execute(select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.position)
                                  .limit(min(limit, 200)).offset(offset))).scalars().all()
    total = (await session.execute(select(func.count()).select_from(Chunk).where(Chunk.document_id == doc_id))).scalar_one()
    return {"document": _row(d), "total": total,
            "chunks": [{"id": c.id, "position": c.position, "page": c.page_no, "words": len(c.content.split()),
                        "question": c.question, "content": c.content} for c in rows]}


class ChunkIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    question: str | None = Field(None, max_length=2000)


async def _save_chunk(session: AsyncSession, chunk: Chunk, body: ChunkIn) -> dict:
    chunk.content, chunk.question = body.content.strip(), (body.question or "").strip() or None
    chunk.embedding = (await embed_texts(session, [chunk.content]))[0]
    chunk.embedding_version = await embedding_signature(session)
    await bump_context_version(session)  # cache + keyword index pick up the edit
    await session.commit()
    return {"id": chunk.id, "position": chunk.position, "page": chunk.page_no, "words": len(chunk.content.split()),
            "question": chunk.question, "content": chunk.content}


async def _ready_doc(session: AsyncSession, doc_id: int) -> Document:
    d = await session.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    return d


@router.put("/{doc_id}/chunks/{chunk_id}", dependencies=[Depends(admin_only)])
async def edit_chunk(doc_id: int, chunk_id: int, body: ChunkIn, session: AsyncSession = Depends(get_session)):
    """Correct the text the assistant answers from. Re-processing the file later replaces manual edits."""
    c = await session.get(Chunk, chunk_id)
    if not c or c.document_id != doc_id:
        raise HTTPException(404, "Not found")
    return await _save_chunk(session, c, body)


@router.post("/{doc_id}/chunks", dependencies=[Depends(admin_only)])
async def add_chunk(doc_id: int, body: ChunkIn, session: AsyncSession = Depends(get_session)):
    d = await _ready_doc(session, doc_id)
    pos = ((await session.execute(select(func.max(Chunk.position)).where(Chunk.document_id == doc_id))).scalar_one() or 0) + 1
    vec = (await embed_texts(session, [body.content.strip()]))[0]  # before add(): the row must never flush without it
    c = Chunk(document_id=doc_id, position=pos, content=body.content.strip(), embedding=vec,
              embedding_version=await embedding_signature(session))
    session.add(c)
    d.chunk_count += 1
    return await _save_chunk(session, c, body)


@router.delete("/{doc_id}/chunks/{chunk_id}", dependencies=[Depends(admin_only)])
async def delete_chunk(doc_id: int, chunk_id: int, session: AsyncSession = Depends(get_session)):
    c = await session.get(Chunk, chunk_id)
    if not c or c.document_id != doc_id:
        raise HTTPException(404, "Not found")
    d = await _ready_doc(session, doc_id)
    await session.delete(c)
    d.chunk_count = max(0, d.chunk_count - 1)
    await bump_context_version(session)
    await session.commit()
    return {"ok": True}


@router.delete("/{doc_id}", dependencies=[Depends(admin_only)])
async def delete(doc_id: int, session: AsyncSession = Depends(get_session)):
    d = await session.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    get_storage().delete(d.file_path)
    await session.delete(d)
    await bump_context_version(session)
    await session.commit()
    return {"ok": True}
