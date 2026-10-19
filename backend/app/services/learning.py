"""Safe learning: questions the assistant could not answer (and replies an admin marked wrong) become review items in
Q&A manager -> Unknown questions. Nothing here ever goes live by itself.

- record_unknown: one row per distinct question. The same or nearly the same wording (normalised form, or trigram
  similarity >= SIMILAR) raises `occurrences` instead of adding a row. Each row gets a suggested category and
  keywords (computed here, kept on the row: guest wording never goes into the keyword index).
- draft_for: a suggested answer for the admin, built from the knowledge the assistant already has (the AI when a
  route is set, else the best matching text). Stored as `draft_answer` only.
- The admin approves through the existing "Add answer" editor: saving it creates the Q&A and marks the row
  approved (api/qa.py). Status: new -> reviewing -> approved | ignored.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UnansweredQuestion
from app.privacy.redaction import Redactor
from app.rag.categories import guess_category
from app.rag.keyword_gen import terms_of
from app.rag.templates import normalize_question

logger = logging.getLogger(__name__)
SIMILAR = 0.8          # pg_trgm similarity of two wordings that count as the same question
OPEN = ("new", "reviewing")
STATUSES = ("new", "reviewing", "approved", "ignored")
REPEATED = 2           # asked this often -> a draft is prepared by the nightly job
MAX_KEYWORDS = 8

DRAFT_SYSTEM = """You help an admin write a saved answer (Q&A) for a customer-service chat.
Write a short, correct answer to QUESTION using ONLY the CONTEXT. Same language as the question (Bangla in Bengali
script for Bangla or Banglish). If the context does not contain the answer, reply exactly: NO_ANSWER.
Never invent prices, dates, addresses, phone numbers or policies."""


def suggested_keywords(question: str) -> list[str]:
    return list(dict.fromkeys(terms_of(question)))[:MAX_KEYWORDS]


async def record_unknown(session: AsyncSession, question: str, *, channel: str | None = None,
                         conversation_id: int | None = None, reason: str = "no_context",
                         wrong_answer: str | None = None) -> UnansweredQuestion:
    """Log (or count again) a question for review. The question is redacted before it is stored."""
    text = Redactor().redact(question or "")[:1000].strip()
    norm = normalize_question(text)
    now = datetime.now(timezone.utc)
    row = None
    if norm:
        row = (await session.execute(select(UnansweredQuestion).where(
            UnansweredQuestion.status.in_(OPEN),
            or_(UnansweredQuestion.question_norm == norm,
                func.similarity(UnansweredQuestion.question_norm, norm) >= SIMILAR))
            .order_by((UnansweredQuestion.question_norm == norm).desc(),
                      func.similarity(UnansweredQuestion.question_norm, norm).desc()).limit(1))).scalar_one_or_none()
    if row is not None:
        row.occurrences = (row.occurrences or 1) + 1
        row.last_seen_at = now
        if wrong_answer:  # feedback outranks a plain "no context": the admin said a reply was wrong
            row.reason, row.wrong_answer = "wrong_answer", Redactor().redact(wrong_answer)[:4000]
        await session.flush()
        return row
    row = UnansweredQuestion(question=text, question_norm=norm, channel=channel, conversation_id=conversation_id,
                             reason=reason, status="new", occurrences=1, last_seen_at=now,
                             suggested_category=guess_category(text), suggested_keywords=suggested_keywords(text),
                             wrong_answer=Redactor().redact(wrong_answer)[:4000] if wrong_answer else None)
    session.add(row)
    await session.flush()
    return row


def set_status(row: UnansweredQuestion, status: str) -> None:
    row.status = status
    row.resolved = status in ("approved", "ignored")


async def draft_for(session: AsyncSession, llm, row: UnansweredQuestion) -> str | None:
    """A suggested answer from the existing knowledge (never published). None when the knowledge has nothing."""
    from app.llm.service import LLMUnavailable
    from app.privacy.guard import PrivacyGuard, PromptInputs
    from app.rag.embeddings import embed_texts
    from app.rag.keyword_search import keyword_retrieve
    from app.rag.search import extractive_answer, hybrid_search

    kw = await keyword_retrieve(session, llm, text=row.question, retrieval_text=row.question, history=[], channel=None,
                                conversation_id=None, is_test=True, allow_ai=False)
    if kw.usable:
        hits, extra = kw.hits, kw.extra
    else:
        qvec = (await embed_texts(session, [row.question]))[0]
        hits, extra = await hybrid_search(session, row.question, qvec, top_k=3, min_similarity=0.25), []
    context = tuple(extra) + tuple((h.text or h.chunk.content) for h in hits)
    if not context:
        return None
    if llm is not None:
        note = f"\nA reply an admin marked WRONG (do not repeat its mistake): {row.wrong_answer}" if row.wrong_answer else ""

        def build(policy):
            guard = PrivacyGuard(policy, history_len=0, chunk_token_budget=1200)
            return guard.prepare(PromptInputs(question=row.question, chunks=context, system_prompt=DRAFT_SYSTEM + note))
        try:
            out = await llm.run(session, "answer", build, channel=None, conversation_id=None, is_test=True)
            text = out.result.text.strip()
            if text and "NO_ANSWER" not in text:
                return text[:4000]
            return None
        except LLMUnavailable:
            pass
    return await extractive_answer(session, row.question, hits, 600) if hits else None


async def prepare_drafts(session: AsyncSession, llm, *, limit: int = 20) -> int:
    """Nightly: repeated questions and wrong-answer feedback without a draft get one. Returns how many were drafted."""
    rows = (await session.execute(select(UnansweredQuestion).where(
        UnansweredQuestion.status.in_(OPEN), UnansweredQuestion.draft_answer.is_(None),
        or_(UnansweredQuestion.occurrences >= REPEATED, UnansweredQuestion.reason == "wrong_answer"))
        .order_by(UnansweredQuestion.occurrences.desc()).limit(limit))).scalars().all()
    n = 0
    for row in rows:
        try:
            draft = await draft_for(session, llm, row)
        except Exception:  # noqa: BLE001 - one bad row must not stop the rest
            logger.exception("draft for unknown question %s failed", row.id)
            continue
        if draft:
            row.draft_answer, n = draft, n + 1
    await session.flush()
    return n


async def refresh_suggestions(session: AsyncSession) -> int:
    """Keyword rebuild: recompute every open row's suggested category / keywords (they live on the row only)."""
    rows = (await session.execute(select(UnansweredQuestion).where(UnansweredQuestion.status.in_(OPEN)))).scalars().all()
    for row in rows:
        row.question_norm = row.question_norm or normalize_question(row.question)
        row.suggested_category = row.suggested_category or guess_category(row.question)
        row.suggested_keywords = suggested_keywords(row.question)
    await session.flush()
    return len(rows)
