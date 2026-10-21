"""Extract (app.rag.extract) -> chunk (300-500 words, small overlap) -> embed -> store. Also the file-storage interface."""
from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Chunk, Document
from app.rag.extract import Section, extract_pages, extract_sections  # noqa: F401 - extract_pages re-exported
from app.rag.embeddings import embed_texts, embedding_signature
from app.services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)


class FileStorage(ABC):
    """Swap for S3/Firebase Storage later without touching callers."""
    @abstractmethod
    def save(self, name: str, data: bytes) -> str: ...
    @abstractmethod
    def read(self, path: str) -> bytes: ...
    @abstractmethod
    def delete(self, path: str) -> None: ...


class LocalStorage(FileStorage):
    def __init__(self, root: str | None = None):
        self.root = Path(root or get_settings().upload_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: bytes) -> str:
        import uuid
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[-80:]
        rel = f"{uuid.uuid4().hex}_{safe}"
        (self.root / rel).write_bytes(data)
        return rel

    def read(self, path: str) -> bytes:
        p = (self.root / path).resolve()
        if self.root.resolve() not in p.parents:  # no path traversal
            raise ValueError("bad path")
        return p.read_bytes()

    def delete(self, path: str) -> None:
        try:
            (self.root / path).unlink()
        except FileNotFoundError:
            pass


def get_storage() -> FileStorage:
    return LocalStorage()


# ---- chunking ---------------------------------------------------------------------------------------
def chunk_pages(pages: list[tuple[int | None, str]], *, target: int = 400, maximum: int = 500,
                overlap: int = 50) -> list[tuple[int | None, str]]:
    """Word-based chunks of ~target words (never above `maximum`) with `overlap` words carried over.
    Each chunk records the page where it starts."""
    words: list[tuple[str, int | None]] = []
    for page, text in pages:
        words += [(w, page) for w in text.split()]
    out: list[tuple[int | None, str]] = []
    i, n = 0, len(words)
    while i < n:
        j = min(i + target, n)
        if n - j < (maximum - target) // 2 + 1 and n - i <= maximum:  # absorb a tiny tail into this chunk
            j = n
        piece = words[i:j]
        text = " ".join(w for w, _ in piece)
        out.append((piece[0][1], text))
        if j >= n:
            break
        i = max(j - overlap, i + 1)
    return out


@dataclass
class ChunkDraft:
    page: int | None
    content: str
    question: str | None = None
    meta: str | None = None


def _is_bangla(s: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in s)


def chunk_sections(sections: list[Section], *, target: int = 120, overlap: int = 20) -> list[ChunkDraft]:
    """Small chunks that never cross a section (a page, a sheet row, a Q&A record). A record's question is repeated
    at the top of every chunk cut from its answer, so each chunk can be found - and understood - on its own."""
    out: list[ChunkDraft] = []
    target = max(30, target)
    for s in sections:
        words = s.text.split()
        if not words:
            continue
        head = ""
        if s.title:
            q_label, a_label = ("প্রশ্ন", "উত্তর") if _is_bangla(s.title) else ("Question", "Answer")
            head = f"{q_label}: {s.title}\n{a_label}: "
        for page, text in chunk_pages([(s.page, s.text)], target=target, maximum=int(target * 1.3), overlap=min(overlap, target // 3)):
            out.append(ChunkDraft(page, head + text, s.title, s.meta))
    return out


# ---- job --------------------------------------------------------------------------------------------
PENDING_NOTE = "Meaning search pending"  # doc.error while some pieces wait for their vectors


def placeholder_vector() -> list[float]:
    """Stands in (the column is NOT NULL) until the real vector is made; never searched: its version is None."""
    from app.models.kb import DIM
    return [1.0] + [0.0] * (DIM - 1)


async def bump_context_version(session: AsyncSession) -> None:
    await set_setting(session, "context_version", int(await get_setting(session, "context_version") or 1) + 1)


async def process_document(session: AsyncSession, doc_id: int, *, storage: FileStorage | None = None, embedder=None) -> str:
    doc = (await session.execute(select(Document).where(Document.id == doc_id)
                                 .with_for_update(skip_locked=True))).scalar_one_or_none()
    if doc is None:  # missing, or another worker is already processing it (no duplicate chunks)
        return "missing"
    storage = storage or get_storage()
    doc.status, doc.error = "processing", None
    await session.flush()
    try:
        # OCR / LibreOffice can take a while: keep the worker's event loop (chat replies) free
        sections = await asyncio.to_thread(extract_sections, storage.read(doc.file_path), doc.type)
        words = int(await get_setting(session, "chunk_words") or 120)
        overlap = int(await get_setting(session, "chunk_overlap_words") or 0)
        chunks = chunk_sections(sections, target=words, overlap=overlap)
        if not chunks:
            raise ValueError("no readable text found in this file")
        await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        version = await embedding_signature(session, embedder=embedder)
        vecs: list[list[float]] = []
        try:
            for i in range(0, len(chunks), 64):
                vecs += await embed_texts(session, [c.content for c in chunks[i:i + 64]], embedder=embedder)
        except Exception as exc:  # noqa: BLE001 - quota / outage: the text is still saved and searchable by its words
            logger.warning("document %s: embedding failed after %d of %d chunks: %s", doc_id, len(vecs), len(chunks), exc)
            doc.error = (f"{PENDING_NOTE} for {len(chunks) - len(vecs)} of {len(chunks)} pieces (embedding "
                         f"provider: {str(exc)[:160]}). Word search works now; it is completed automatically.")
        for pos, c in enumerate(chunks):
            # a piece without a vector waits with no version (services/embedding_maintenance.embed_pending fills it)
            vec = vecs[pos] if pos < len(vecs) else placeholder_vector()
            session.add(Chunk(document_id=doc.id, position=pos, content=c.content, question=c.question, keywords=c.meta,
                              page_no=c.page, embedding=vec, embedding_version=version if pos < len(vecs) else None))
        doc.chunk_count, doc.status = len(chunks), "ready"
        # a newer version replaces older ones with the same title
        old = (await session.execute(select(Document.id).where(Document.title == doc.title, Document.id != doc.id,
                                                               Document.status == "ready", Document.id < doc.id))).scalars().all()
        if old:
            await session.execute(delete(Chunk).where(Chunk.document_id.in_(old)))
            await session.execute(update(Document).where(Document.id.in_(old)).values(status="superseded", chunk_count=0))
        await bump_context_version(session)
        await session.commit()
        return "ready"
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        doc = await session.get(Document, doc_id)
        doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        await session.commit()
        logger.exception("document %s failed", doc_id)
        return "failed"
