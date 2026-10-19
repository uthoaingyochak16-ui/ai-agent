"""Fast wording-only Q&A lookup.

The index deliberately contains only stable text metadata. The winning QAPair is fetched by primary key so an
answer/template edit is never served from a stale ORM object. `context_version` invalidates the index after every
Q&A create, edit, import, or delete.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QAPair
from app.rag.search import _matches, content_words, coverage
from app.rag.search import norm as search_norm
from app.rag.variables import NEUTRAL_WORDS
from app.services.settings_service import get_setting


@dataclass(frozen=True)
class QAEntry:
    id: int
    question: str
    question_norm: str
    wording_norm: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class QAIndex:
    by_norm: dict[str, QAEntry]
    by_id: dict[int, QAEntry]


_INDEX: dict[int, tuple[int, QAIndex]] = {}


async def active_qa_index(session: AsyncSession) -> QAIndex:
    version = int(await get_setting(session, "context_version") or 1)
    bind = session.bind
    engine = getattr(bind, "engine", bind)
    key = id(engine)  # one shared engine in production; avoids cross-test-session stale state
    cached = _INDEX.get(key)
    if cached and cached[0] == version:
        return cached[1]
    rows = (await session.execute(select(QAPair.id, QAPair.question, QAPair.question_norm)
                                  .where(QAPair.active.is_(True)))).all()
    entries = [QAEntry(row.id, row.question, row.question_norm, search_norm(row.question),
                       tuple(dict.fromkeys(content_words(row.question)))) for row in rows]
    index = QAIndex({entry.question_norm: entry for entry in entries}, {entry.id: entry for entry in entries})
    _INDEX[key] = (version, index)
    return index


async def trigram_candidates(session: AsyncSession, text_norm: str, index: QAIndex, *, limit: int = 20) -> list[QAEntry]:
    """Use pg_trgm's indexed `%` operator, then do exact matching policy in Python on just these rows."""
    if not text_norm:
        return []
    similarity = func.similarity(QAPair.question_norm, text_norm)
    ids = (await session.execute(select(QAPair.id).where(QAPair.active.is_(True),
                                  QAPair.question_norm.op("%")(text_norm))
                                 .order_by(similarity.desc(), QAPair.id).limit(limit))).scalars().all()
    return [index.by_id[qid] for qid in ids if qid in index.by_id]


async def match_qa_text(session: AsyncSession, text: str) -> tuple[QAPair | None, float]:
    """Exact, near-wording, and coverage matching without scanning all Q&A ORM rows per message."""
    from app.rag.templates import normalize_question

    norm = normalize_question(text)
    if not norm:
        return None, 1.0
    index = await active_qa_index(session)
    exact = index.by_norm.get(norm)
    if exact is not None:
        return await session.get(QAPair, exact.id), 1.0

    candidates = await trigram_candidates(session, norm, index)
    if not candidates:
        return None, 1.0
    qn = search_norm(text)
    best: tuple[QAEntry | None, float] = (None, 0.0)
    for candidate in candidates:
        score = __import__("difflib").SequenceMatcher(None, qn, candidate.wording_norm).ratio()
        if score > best[1]:
            best = (candidate, score)
    if best[0] is not None and best[1] >= float(await get_setting(session, "qa_text_threshold") or 0.86):
        return await session.get(QAPair, best[0].id), round(best[1], 4)

    query_words = content_words(text)
    coverage_best: tuple[QAEntry | None, float] = (None, 0.0)
    for candidate in candidates:
        candidate_words = list(candidate.words)
        matched, total = coverage(candidate_words, text)
        extra = [word for word in set(query_words) if word not in NEUTRAL_WORDS and not _matches(word, set(candidate_words))]
        if total >= 2 and matched == total and not extra:
            score = total / max(total, len(set(query_words)))
            if coverage_best[0] is None or score > coverage_best[1]:
                coverage_best = (candidate, score)
    if coverage_best[0] is not None:
        return await session.get(QAPair, coverage_best[0].id), round(coverage_best[1], 4)
    return None, 1.0
