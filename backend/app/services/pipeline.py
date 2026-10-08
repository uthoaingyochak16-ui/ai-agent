"""Reply pipeline, optimized for LOW token cost. Stops at the first step that can answer:
   1. Q&A shortcut (exact / very-high similarity)  -> 0 LLM calls
   2. semantic cache                               -> 0 LLM calls
   3. retrieval (top-k chunks, token-budgeted); nothing relevant -> canned "don't know" (0 LLM calls)
   4. minimal guarded prompt -> small model, escalate to the large one on low confidence / complex questions
   5. output filter (leaks) and logging of every call
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.models import (Chunk, Conversation, Document, Profile, QAPair, SemanticCache, UnansweredQuestion, Variable)
from app.privacy.guard import (DEFAULT_SYSTEM_PROMPT, PrivacyGuard, PromptInputs, check_output)
from app.privacy.redaction import Redactor
from app.privacy.summary import compose_summary, missing_fields
from app.rag.embeddings import embed_texts
from app.rag.search import _matches, content_words, coverage, extractive_answer, hybrid_search, shorten
from app.rag.search import norm as search_norm
from app.rag.templates import fill_template, normalize_question
from app.rag.variables import NEUTRAL_WORDS, match_variable, variable_answer
from app.services import consent as consent_svc
from app.services.prompt_context import load_profile_view
from app.services.language import localized
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")


@dataclass
class PipelineResult:
    text: str
    path: str  # qa_shortcut | variable | cache | llm | llm_large | no_context | kb_extract (no AI model: text from the file)
    tokens_in: int | None = None
    tokens_out: int | None = None
    debug: dict = field(default_factory=dict)


def _is_complex(q: str) -> bool:
    return len(q.split()) > 40 or q.count("?") >= 2 or bool(re.search(r"(?i)\b(compare|difference|versus|vs)\b|তুলনা|পার্থক্য", q))


async def run_pipeline(session: AsyncSession, llm: LLMService, *, text: str, profile: Profile | None,
                       conversation: Conversation | None, channel: str | None,
                       history: list[tuple[str, str]] | None = None, persist: bool = True, is_test: bool = False,
                       embedder=None, knowledge_only: bool = False) -> PipelineResult:
    t0 = time.perf_counter()
    dbg: dict = {"steps": []}
    get = lambda k: get_setting(session, k)  # noqa: E731

    def done(res: PipelineResult) -> PipelineResult:
        res.debug["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        res.debug["path"] = res.path
        return res

    var_rows = list((await session.execute(select(Variable))).scalars())
    variables = {v.key: v.value for v in var_rows}
    norm = normalize_question(text)

    # ---- 1a. exact Q&A (no embedding, no LLM) -------------------------------------------------------
    max_chars = int(await get("max_answer_chars") or 0)
    qa = (await session.execute(select(QAPair).where(QAPair.active.is_(True), QAPair.question_norm == norm)
                                .limit(1))).scalar_one_or_none() if norm else None
    sim = 1.0
    if qa is None and norm:
        # ---- 1a'. near-identical wording (typos, punctuation, Bangla spelling variants): plain text similarity,
        # so it works even with the offline embedder
        qn, best = search_norm(text), (None, 0.0)
        for cand in (await session.execute(select(QAPair).where(QAPair.active.is_(True)))).scalars():
            s = SequenceMatcher(None, qn, search_norm(cand.question)).ratio()
            if s > best[1]:
                best = (cand, s)
        if best[0] is not None and best[1] >= float(await get("qa_text_threshold") or 0.86):
            qa, sim = best[0], round(best[1], 4)
    if qa is None and norm:
        # Customers often add the organisation name to a short saved question, e.g.
        # "Quantum Method course fee koto?" for the stored "Course fee koto?".
        # Trust this only when every meaningful word in a 2+ word saved question is present.
        query_words = content_words(text)
        best = None
        for cand in (await session.execute(select(QAPair).where(QAPair.active.is_(True)))).scalars():
            cand_words = list(dict.fromkeys(content_words(cand.question)))
            matched, total = coverage(cand_words, text)
            # ...and nothing else that changes the subject: "Yoga course fee koto?" is not "Course fee koto?"
            extra = [w for w in set(query_words) if w not in NEUTRAL_WORDS and not _matches(w, set(cand_words))]
            if total >= 2 and matched == total and not extra:
                score = total / max(total, len(set(query_words)))
                if best is None or score > best[1]:
                    best = (cand, score)
        if best is not None:
            qa, sim = best[0], round(best[1], 4)
    if qa is not None:
        filled, missing = fill_template(qa.answer_template, variables)
        if not missing:
            dbg["qa_match"] = {"id": qa.id, "question": qa.question, "similarity": sim, "exact": sim == 1.0, "category": qa.category}
            return done(PipelineResult(shorten(filled, max_chars), "qa_shortcut", debug=dbg))
        dbg["steps"].append(f"Q&A {qa.id} skipped: unresolved variables {missing}")

    # ---- embed the (redacted) question once; reused by every later step -----------------------------------
    query_text = text
    if len(text.split()) < 4 and history:  # short follow-up: add the previous user turn for retrieval
        prev = [t for r, t in history if r == "user"]
        if prev:
            query_text = f"{prev[-1]} {text}"
    qvec = (await embed_texts(session, [query_text], embedder=embedder))[0]

    # ---- 1b. very-high-similarity Q&A -------------------------------------------------------------------
    qa_thr = float(await get("qa_threshold"))
    rows = (await session.execute(
        select(QAPair, QAPair.embedding.cosine_distance(qvec).label("d")).where(QAPair.active.is_(True))
        .order_by("d").limit(3))).all()
    dbg["qa_candidates"] = [{"id": q.id, "question": q.question, "similarity": round(1 - d, 4)} for q, d in rows]
    if rows and 1 - rows[0].d >= qa_thr:
        q = rows[0][0]
        filled, missing = fill_template(q.answer_template, variables)
        if not missing:
            dbg["qa_match"] = {"id": q.id, "question": q.question, "similarity": round(1 - rows[0].d, 4), "exact": False, "category": q.category}
            return done(PipelineResult(shorten(filled, max_chars), "qa_shortcut", debug=dbg))

    # ---- 1c. the question asks for a value staff keep in Variables (fee, phone, date...) -> that value -------------
    # (before the cache and the documents: a document's copy of a price may be older than the variable)
    vm = match_variable(text, var_rows)
    if vm is not None:
        dbg["variable_match"] = {"key": vm[0].key, "score": round(vm[1], 3)}
        return done(PipelineResult(variable_answer(vm[0]), "variable", debug=dbg))

    # ---- 2. semantic cache -------------------------------------------------------------------------------------
    ctx_version = int(await get("context_version") or 1)
    ttl = timedelta(days=float(await get("cache_ttl_days") or 14))
    hit = (await session.execute(
        select(SemanticCache, SemanticCache.embedding.cosine_distance(qvec).label("d"))
        .where(SemanticCache.context_version == ctx_version, SemanticCache.created_at >= datetime.now(timezone.utc) - ttl)
        .order_by("d").limit(1))).first()
    if hit and 1 - hit.d >= float(await get("cache_threshold")):
        if persist:
            await session.execute(update(SemanticCache).where(SemanticCache.id == hit[0].id).values(hits=SemanticCache.hits + 1))
        dbg["cache"] = {"id": hit[0].id, "similarity": round(1 - hit.d, 4), "cached_question": hit[0].question}
        return done(PipelineResult(hit[0].answer, "cache", debug=dbg))

    # ---- 3. retrieval: vector + keyword (hybrid) ------------------------------------------------------------------------
    top_k = int(await get("top_k"))
    min_sim = float(await get("retrieval_min_similarity"))
    hits = await hybrid_search(session, text, qvec, top_k=top_k, min_similarity=min_sim)
    chunks = [(h.chunk, h.document, h.score) for h in hits]
    confidence = min(1.0, chunks[0][2]) if chunks else 0.0
    dbg["chunks"] = [{"chunk_id": h.chunk.id, "document": h.document.title, "document_id": h.document.id, "page": h.chunk.page_no,
                      "similarity": h.score, "vector": h.vector, "keywords": f"{h.matched}/{h.total}",
                      "question_match": h.question_sim, "preview": h.chunk.content[:160]} for h in hits]
    dbg["retrieval_confidence"] = round(confidence, 4)
    if not chunks:
        if persist and not is_test:
            session.add(UnansweredQuestion(question=Redactor().redact(text)[:1000], channel=channel,
                                           conversation_id=conversation.id if conversation else None))
        return done(PipelineResult(await localized(session, "no_answer_reply", text), "no_context", debug=dbg))

    # During a human handover, keep answering only when the stored knowledge itself can produce a
    # high-confidence answer. Never call a generative model or guess while a staff member owns the chat.
    if knowledge_only:
        answer = await extractive_answer(session, text, hits, max_chars or 450)
        if answer:
            return done(PipelineResult(answer, "kb_extract", debug=dbg))
        if persist and not is_test:
            session.add(UnansweredQuestion(question=Redactor().redact(text)[:1000], channel=channel,
                                           conversation_id=conversation.id if conversation else None))
        return done(PipelineResult(await localized(session, "no_answer_reply", text), "no_context", debug=dbg))

    # ---- 4. minimal guarded prompt -> LLM ---------------------------------------------------------------------------------
    view = await load_profile_view(session, profile) if profile is not None and profile.id is not None else None
    hist_len = int(await get("history_len"))
    if history is None and conversation is not None:
        from app.models import Message
        rows_ = (await session.execute(select(Message).where(Message.conversation_id == conversation.id,
                 Message.role.in_(("user", "assistant", "agent"))).order_by(Message.id.desc()).limit(hist_len + 1))).scalars().all()
        history = [("assistant" if m.role == "agent" else m.role, m.content_redacted) for m in reversed(rows_)]
        if history and history[-1][0] == "user":  # the current message is already stored: exclude it
            history = history[:-1]
    history = (history or [])[-hist_len:]
    chunk_budget, system_prompt = int(await get("chunk_token_budget")), (await get("system_prompt")) or DEFAULT_SYSTEM_PROMPT
    if max_chars:  # chat replies stay short; the guest can ask for more
        system_prompt += (f"\nKeep the answer short: at most 3 sentences and about {max_chars} characters, "
                          "unless the user explicitly asks for details.")
    conv_summary = conversation.summary if conversation is not None else None
    consent_ok = view is not None and view.consent_state == "given"
    capture: dict = {}

    def build(policy):
        guard = PrivacyGuard(policy, history_len=hist_len, chunk_token_budget=chunk_budget)
        known = (profile.name,) if profile is not None and profile.name else ()
        rd = guard.new_redactor(known_names=known)
        summary = compose_summary(view, rd, policy.never_send) if view else None
        missing = missing_fields(view, policy.never_send) if consent_ok else ()
        capture["summary"], capture["missing"] = summary, missing
        return guard.prepare(PromptInputs(question=text, profile_summary=summary or None, missing_fields=missing,
                                          recent_messages=tuple(history), conversation_summary=conv_summary,
                                          chunks=tuple(c.content for c, _, _ in chunks), system_prompt=system_prompt),
                             redactor=rd)

    large = confidence < float(await get("escalate_below")) or _is_complex(text)
    task = "answer_large" if large else "answer"
    dbg["tier"] = "large" if large else "small"
    dbg["profile_injected"] = {"summary": None, "missing_fields": []}
    try:
        try:
            out = await llm.run(session, task, build, channel=channel, conversation_id=conversation.id if conversation else None, is_test=is_test)
        except LLMUnavailable as exc:
            if task == "answer_large":  # no large route configured / failed: try the standard one
                out = await llm.run(session, "answer", build, channel=channel, conversation_id=conversation.id if conversation else None, is_test=is_test)
                task = "answer"
            else:
                raise exc
    except LLMUnavailable as exc:
        # no AI model (not configured, failed, or budget stop): answer straight from the best matching text
        dbg["error"] = str(exc)
        answer = await extractive_answer(session, text, hits, max_chars or 450)
        if answer:
            return done(PipelineResult(answer, "kb_extract", debug=dbg))
        return done(PipelineResult(await localized(session, "no_answer_reply", text), "llm_unavailable", debug=dbg))

    own = frozenset(v for v in (consent_svc.get_phone(profile) if profile and profile.phone_enc else None,
                                consent_svc.get_email(profile) if profile and profile.email_enc else None) if v)
    chk = check_output(out.result.text, out.request, own_values=own, allowed_values=frozenset(variables.values()))
    dbg.update({"profile_injected": {"summary": capture.get("summary"), "missing_fields": list(capture.get("missing", ()))},
                "prompt": out.request.audit_payload, "redactions": out.request.report["redactions"],
                "provider": out.result.provider, "model": out.result.model, "task": task,
                "tokens_in": out.result.tokens_in, "tokens_out": out.result.tokens_out, "cost_usd": float(out.cost_usd),
                "fallback_used": out.fallback_used, "budget_fallback": out.budget_fallback,
                "output_blocked": chk.blocked, "output_reasons": chk.reasons, "est_tokens_in": out.request.report["est_tokens_in"]})

    # ---- cache only what is safe to share between users ------------------------------------------------------------------------
    if persist and not chk.blocked and not out.request.report["redactions"] and not _PLACEHOLDER.search(out.result.text):
        session.add(SemanticCache(question=text[:500], answer=chk.text, embedding=qvec, context_version=ctx_version))
        dbg["cached"] = True
    if chk.blocked and persist and not is_test:
        session.add(UnansweredQuestion(question=Redactor().redact(text)[:1000], channel=channel,
                                       conversation_id=conversation.id if conversation else None, reason="output_blocked"))
    return done(PipelineResult(chk.text, "llm_large" if task == "answer_large" else "llm", out.result.tokens_in, out.result.tokens_out, dbg))
