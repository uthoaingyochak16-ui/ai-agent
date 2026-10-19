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
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.models import (Chunk, Conversation, Document, Profile, QAPair, SemanticCache, Variable)
from app.privacy.guard import (DEFAULT_SYSTEM_PROMPT, PrivacyGuard, PromptInputs, check_output)
from app.privacy.summary import compose_summary, missing_fields
from app.rag.embeddings import embed_texts, embedding_signature
from app.rag.keyword_search import _record_text as record_text
from app.rag.keyword_search import keyword_retrieve
from app.rag.programs import (compose_answer, context_line, match_program_question, quote_program_names, today_bd,
                              upcoming)
from app.rag.qa_index import match_qa_text
from app.rag.search import extractive_answer, hybrid_search, shorten
from app.rag.templates import fill_known, fill_template, normalize_question
from app.rag.variables import match_variable, variable_answer
from app.services import consent as consent_svc
from app.services import team_instructions
from app.services.answer_cache import same_topic
from app.services.continuation import CONTINUE_NOTE, reply_to_our_question
from app.services.learning import record_unknown
from app.services.reply_style import pre_classify, tone_for
from app.services.tools import TOOL_ROUNDS, ToolContext, enabled_tools, run_tool_round, tools_prompt
from app.services.prompt_context import load_profile_view
from app.services.language import localized
from app.services.language import detect_in_conversation
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")
_FOLLOWUP_REF = re.compile(r"(?i)(?:\b(?:oita|eta|seta|oi|it|that|this|also|ar)\b|ঐটা|এটা|সেটা|আর)")
REWRITE_SYSTEM_PROMPT = """Rewrite a customer follow-up into one self-contained question for knowledge retrieval.
Use the same language as the customer's new message. Use only facts needed from the recent conversation.
Do not answer the question, add no explanation, and output ONLY the rewritten question.
If the new message answers a question the assistant just asked (yes / no / ok / a choice / a place), rewrite it as the
request that answer implies, e.g. ASSISTANT: "Are you interested in the course?" CUSTOMER: "ji" -> "I am interested in
the Quantum Method course; how can I join it?".
If the new message is already self-contained, return it unchanged."""

GENERAL_ANSWER_RULES = """
GENERAL ANSWER MODE: This section OVERRIDES rule 1 above. Answer ordinary general-knowledge questions accurately
and helpfully using your own knowledge; do not refuse merely because they are outside the organisation's services.
Use CONTEXT only when it directly answers the question; ignore unrelated retrieved text. Do not present general
knowledge as information about this organisation, and do not invent its policies, prices, availability, contact details,
services, or commitments. If the question specifically asks about this organisation and no directly relevant context is
available, reply with exactly NO_ANSWER and nothing else."""

# The knowledge search hands over every candidate that might fit; the model is the judge of whether one answers.
CONTEXT_CHECK_RULES = """
ANSWER CHECK (this replaces every other instruction about what to say when you do not know): First decide whether
CONTEXT contains what the person asked for - the same office, place, program, service, person or topic, not merely a
similar one. If it does, answer from it (combine pieces when needed). If it holds only part of it, give that part and
say which part you do not have. If nothing in CONTEXT answers the question, your whole reply must be exactly NO_ANSWER:
no apology, no explanation, no offer of a team member - the system sends the standard reply and logs the question for
the team. A question about the person themself ("what is my name?", "what do you know about me?") is answered from
PROFILE (or by saying you do not have that detail), never with NO_ANSWER."""
ANSWER_CHECK_NOTE = ("ANSWER CHECK: if CONTEXT does not contain the answer to this QUESTION (the same office, place, "
                     "program, service or topic), reply with exactly NO_ANSWER and nothing else.")
# A reply that names the internal CONTEXT block ("CONTEXT-এ এ বিষয়ে তথ্য নেই") is the model explaining that the
# knowledge does not hold the answer: the same as NO_ANSWER, and the guest must never see that word.
NO_ANSWER = re.compile(r"\bNO_ANSWER\b|\bCONTEXT\b")
# models now and then write a Devanagari digit inside a Bangla number ("০২ ৪८३২..."); it reads the same but would fail
# the contact check (and is wrong to send), so it becomes the Bangla digit
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "০১২৩৪৫৬৭৮৯")


@dataclass
class PipelineResult:
    text: str
    path: str  # qa_shortcut | program | variable | cache | llm | llm_large | no_context | kb_extract (no AI model: text from the file)
    tokens_in: int | None = None
    tokens_out: int | None = None
    debug: dict = field(default_factory=dict)


def _needs_rewrite(text: str, history: list[tuple[str, str]]) -> bool:
    return bool(history) and (len(text.split()) <= 5 or bool(_FOLLOWUP_REF.search(text)))


# words that make a question about the organisation itself (its offices, courses, fees, people...), not general
# knowledge. Bangla: substring (inflections); Latin: whole word.
_ORG_BN = ("কোয়ান্টাম", "ফাউন্ডেশন", "আপনাদের", "তোমাদের", "অফিস", "শাখা", "সেন্টার", "ঠিকানা", "যোগাযোগ", "কোর্স",
           "ফি", "রেজিস্ট্রেশন", "ভর্তি", "ব্যাচ", "প্রোগ্রাম", "ল্যাব", "সাদাকায়ন", "হটলাইন", "নম্বর", "নাম্বার")
_ORG_LATIN = {"quantum", "foundation", "apnader", "apnadr", "tomader", "your", "office", "branch", "center", "centre",
              "address", "thikana", "contact", "jogajog", "course", "kors", "fee", "fees", "registration", "bhorti",
              "admission", "batch", "program", "programme", "lab", "sadakaion", "hotline", "number", "nambar",
              "refund", "policy", "price", "schedule", "director", "founder"}
_PAYMENT_ACTION = re.compile(
    r"(?i)\b(payments?|pay|paid|bkash|bkas|nagad|rocket|bank|transactions?|refunds?|receipts?|instal+ments?|cash\s?out)\b|"
    r"\b(?:taka|money)\s+(?:dibo|dite|pathabo|pathaisi|send|sent|ferot|kete)\b|"
    r"পেমেন্ট|পরিশোধ|বিকাশ|নগদ|রকেট|ব্যাংক|লেনদেন|ট্রানজ্যাকশন|রিফান্ড|রসিদ|রিসিট|কিস্তি|টাকা\s*(?:পাঠ|দেব|দিতে|ফেরত|কেটে)"
)


def is_org_specific(q: str) -> bool:
    t = (q or "").lower()
    return any(w in t for w in _ORG_BN) or bool(_ORG_LATIN & set(re.findall(r"[a-z]+", t)))


def _is_payment_action(q: str) -> bool:
    """Payment handling is never automated; asking what something costs is an ordinary question."""
    return bool(_PAYMENT_ACTION.search(q or ""))


def _is_complex(q: str) -> bool:
    return len(q.split()) > 40 or q.count("?") >= 2 or bool(re.search(r"(?i)\b(compare|difference|versus|vs)\b|তুলনা|পার্থক্য", q))


PROGRAM_RULES = """
PROGRAMS: lines starting with "PROGRAM" are the organisation's upcoming programs from its schedule, nearest first
(programs that are over are not listed). Today is {today}. Use them for any question about programs, courses or events:
for "next" pick the nearest one that fits what the guest asked (the kind of program, the city ...), compare dates when
asked, and mention how many days are left when useful. Guests spell program names in many ways ("proga jalali",
"songghayon", "sadakayon"): match by meaning and sound, but never take a different program because a word looks alike.
If a detail says "not listed", say it is not announced yet; a fee may still be given elsewhere in CONTEXT (a VALUE or
Q&A line). Do not list every program unless the guest asks for a list. Write a program's name exactly as given."""
# how many upcoming programs every answer prompt carries (the matched ones first)
PROGRAM_LINES = 30


async def run_pipeline(session: AsyncSession, llm: LLMService, *, text: str, profile: Profile | None,
                       conversation: Conversation | None, channel: str | None,
                       history: list[tuple[str, str]] | None = None, persist: bool = True, is_test: bool = False,
                       embedder=None, knowledge_only: bool = False, combined_turns: int = 1,
                       standalone_question: str | None = None, guest_request: str | None = None,
                       guest_language: str | None = None) -> PipelineResult:
    """combined_turns: how many trailing stored user messages make up `text` (a debounced burst); they are the
    question, so they are left out of the history. standalone_question: the intent router already rewrote a
    follow-up into a self-contained question, so the separate "rewrite" call is skipped. guest_request: the router's
    one-line reading of what exactly the guest wants; the answering model gets it next to the guest's own words."""
    t0 = time.perf_counter()
    dbg: dict = {"steps": []}
    get = lambda k: get_setting(session, k)  # noqa: E731
    if guest_request:
        dbg["guest_request"] = guest_request
    # what the answering model reads as the question: the guest's words, plus the router's reading of them
    asked = f"{text}\n\n(WHAT THE GUEST WANTS: {guest_request})" if guest_request else text

    def done(res: PipelineResult) -> PipelineResult:
        res.debug["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        res.debug["path"] = res.path
        return res

    # Load prior turns before retrieval so ambiguous follow-ups can be made standalone. Stored current/debounced
    # user turns are explicitly removed; the original message stays in storage and in the final answer prompt.
    hist_len = int(await get("history_len") or 0)
    if history is None and conversation is not None:
        from app.models import Message
        rows_ = (await session.execute(select(Message).where(Message.conversation_id == conversation.id,
                 Message.role.in_(("user", "assistant", "agent"))).order_by(Message.id.desc())
                 .limit(max(hist_len, 4) + combined_turns))).scalars().all()
        history = [("assistant" if m.role == "agent" else m.role, m.content_redacted) for m in reversed(rows_)]
        for _ in range(max(1, combined_turns)):
            if history and history[-1][0] == "user":
                history = history[:-1]
    history = history or []
    # "ji" / "Ok" / "hya, dhakay" right after the assistant asked something: the answer to that question, not a request
    followup_of = reply_to_our_question(text, history)
    if followup_of:
        dbg["steps"].append(f"reply to the assistant's question ({followup_of[:80]}): continuing from there")
    # the router read the whole message in context ("Dhakai?", "fee?" are Banglish): its language wins; Banglish -> Bangla
    reply_lang = {"en": "en", "bn": "bn", "banglish": "bn"}.get(guest_language or "") or detect_in_conversation(text, history)
    rewrite_history = history[-max(1, min(4, hist_len or 4)):]
    retrieval_text = text
    rewrite_used = False
    if standalone_question and standalone_question.strip():
        retrieval_text = standalone_question.strip()[:4000]
        rewrite_used = retrieval_text != text.strip()
        dbg["steps"].append("standalone question from the intent router")
    elif _needs_rewrite(text, rewrite_history):
        try:
            def rewrite_build(policy):
                guard = PrivacyGuard(policy, history_len=len(rewrite_history), chunk_token_budget=0)
                return guard.prepare(PromptInputs(question=text, recent_messages=tuple(rewrite_history), chunks=(),
                                                  system_prompt=REWRITE_SYSTEM_PROMPT))
            rewritten = (await llm.run(session, "rewrite", rewrite_build, channel=channel,
                                       conversation_id=conversation.id if conversation else None,
                                       is_test=is_test)).result.text.strip().strip("` ")
            if rewritten:
                retrieval_text, rewrite_used = rewritten[:4000], rewritten.strip() != text.strip()
        except LLMUnavailable as exc:
            # Preserve the prior short-follow-up retrieval fallback if rewriting is not configured.
            dbg["steps"].append(f"Rewrite unavailable: {exc}")
            if len(text.split()) < 4:
                prior_users = [turn for role, turn in history if role == "user"]
                if prior_users:
                    retrieval_text = f"{prior_users[-1]} {text}"
        except Exception as exc:  # optional enhancement; answer delivery must never depend on it
            logger.warning("follow-up rewrite failed; using original retrieval text: %s", exc)
    dbg["question_rewrite"] = {"original": text, "rewritten": retrieval_text, "used": rewrite_used}

    # Payment instructions, transfers, refunds, receipts and instalments are deliberately out of scope.  This is
    # before Q&A/cache/LLM so an old saved response can never disclose a payment route. A fee question ("how much?")
    # is an ordinary question: saved Q&A, Variables, the schedule and the knowledge answer it.
    if _is_payment_action(retrieval_text):
        dbg["steps"].append("payment action blocked by policy")
        if persist and not is_test:
            await record_unknown(session, text, channel=channel, conversation_id=conversation.id if conversation else None,
                                 reason="payment_disabled")
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "payment_disabled", debug=dbg))

    var_rows = list((await session.execute(select(Variable))).scalars())
    variables = {v.key: v.value for v in var_rows if (v.value or "").strip()}  # an empty value is not set yet
    norm = normalize_question(retrieval_text)
    # a matched Q&A whose answer needs a variable with no value is not sent as is; what remains of it is handed to the
    # AI with the retrieved knowledge (step 4), which fills the gap from the documents or says it does not know
    qa_hints: list[str] = []

    def qa_hint(q: QAPair) -> None:
        if (rest := fill_known(q.answer_template, variables)) and (line := f"Q&A — {q.question}\n{rest}") not in qa_hints:
            qa_hints.append(line)

    # ---- 1a. exact Q&A (no embedding, no LLM) -------------------------------------------------------
    max_chars = int(await get("max_answer_chars") or 0)

    def qa_reply(q: QAPair, similarity: float, exact: bool) -> PipelineResult | None:
        filled, missing = fill_template(q.answer_template, variables)
        if not missing:
            dbg["qa_match"] = {"id": q.id, "question": q.question, "similarity": similarity, "exact": exact, "category": q.category}
            return PipelineResult(shorten(filled, max_chars), "qa_shortcut", debug=dbg)
        qa_hint(q)
        dbg["steps"].append(f"Q&A {q.id}: variables without a value {missing}; the rest of its answer goes to the AI")
        return None

    qa, sim = await match_qa_text(session, retrieval_text)
    if qa is not None and (res := qa_reply(qa, sim, sim == 1.0)):
        return done(res)

    # ---- embed the (redacted) question once; reused by every later step -----------------------------------
    # The embedding provider can fail (quota, outage). Guests must still be answered: without a vector, the meaning
    # search, vector Q&A match and answer cache are skipped and the word searches + the AI carry the answer.
    try:
        qvec = (await embed_texts(session, [retrieval_text], embedder=embedder))[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("question embedding failed; answering with word search only: %s", exc)
        dbg["steps"].append(f"embedding unavailable ({str(exc)[:80]}): word search only")
        qvec = None
    embedding_version = await embedding_signature(session, embedder=embedder)

    # ---- 1b. very-high-similarity Q&A -------------------------------------------------------------------
    qa_thr = float(await get("qa_threshold"))
    rows = (await session.execute(
        select(QAPair, QAPair.embedding.cosine_distance(qvec).label("d")).where(
            QAPair.active.is_(True), QAPair.embedding_version == embedding_version)
        .order_by("d").limit(3))).all() if qvec is not None else []
    dbg["qa_candidates"] = [{"id": q.id, "question": q.question, "similarity": round(1 - d, 4)} for q, d in rows]
    if rows and 1 - rows[0].d >= qa_thr and (res := qa_reply(rows[0][0], round(1 - rows[0].d, 4), False)):
        return done(res)

    # ---- 1c'. upcoming programs: every answer sees the schedule, so the AI (not a word match) decides which program a
    # question or a follow-up ("kothay hobe?") is about. A program the wording names leads the list.
    today = today_bd()
    programs = await upcoming(session, today)
    pq = match_program_question(retrieval_text, programs)
    if pq is not None:
        dbg["program_match"] = {"ids": [p.id for p in pq.programs], "generic": pq.generic,
                                "names": [p.name for p in pq.programs], "wants_date": pq.wants_date,
                                "wants_fee": pq.wants_fee, "wants_place": pq.wants_place}
    lead = pq.programs if pq is not None else []
    program_ctx = [context_line(p, today) for p in (lead + [p for p in programs if p not in lead])[:max(PROGRAM_LINES, len(lead))]]
    # saved values (course fee, phones, links ...) are short and exact: always part of what the AI may quote
    value_ctx = ["VALUE — " + variable_answer(v) for v in var_rows if (v.value or "").strip()]

    # ---- 1c. the question asks for a value staff keep in Variables (fee, phone, date...) -> that value -------------
    # (before the cache and the documents: a document's copy of a price may be older than the variable)
    vm = match_variable(retrieval_text, var_rows)
    if vm is not None:
        dbg["variable_match"] = {"key": vm[0].key, "score": round(vm[1], 3)}
        return done(PipelineResult(variable_answer(vm[0]), "variable", debug=dbg))

    # ---- 2. semantic cache -------------------------------------------------------------------------------------
    ctx_version = int(await get("context_version") or 1)
    ttl = timedelta(days=float(await get("cache_ttl_days") or 14))
    # auto entries expire with the knowledge (context_version) and TTL; admin-verified ones don't; blocked never serve
    servable = or_(SemanticCache.status == "verified",
                   and_(SemanticCache.status == "auto", SemanticCache.context_version == ctx_version,
                        SemanticCache.created_at >= datetime.now(timezone.utc) - ttl))
    cache_thr = float(await get("cache_threshold"))
    candidates = (await session.execute(
        select(SemanticCache, SemanticCache.embedding.cosine_distance(qvec).label("d"))
        .where(servable, SemanticCache.embedding_version == embedding_version)
        .order_by("d").limit(5))).all() if qvec is not None else []
    for entry, dist in candidates:
        if 1 - dist < cache_thr:
            break
        if not same_topic(retrieval_text, entry.question):  # close vectors, different subject ("fee" vs "yoga fee")
            dbg["steps"].append(f"cache {entry.id} skipped: different topic words")
            continue
        if persist:
            await session.execute(update(SemanticCache).where(SemanticCache.id == entry.id).values(hits=SemanticCache.hits + 1))
        dbg["cache"] = {"id": entry.id, "similarity": round(1 - dist, 4), "cached_question": entry.question,
                        "status": entry.status}
        return done(PipelineResult(entry.answer, "cache", debug=dbg))

    # ---- 3. retrieval: every search finds candidates; the answer step decides whether one of them answers ----------------
    # Keyword index (records described by keywords) + hybrid search (meaning vectors + words) together, so a new document
    # is found whatever words the guest uses. Whether a candidate really answers the question is not decided by word
    # overlap here: the AI reads the candidates and answers from them, or says NO_ANSWER (step 4).
    top_k = int(await get("top_k"))
    min_sim = float(await get("retrieval_min_similarity"))
    kw = None
    if await get("keyword_search_enabled"):
        kw = await keyword_retrieve(session, llm, text=text, retrieval_text=retrieval_text, history=rewrite_history,
                                    channel=channel, conversation_id=conversation.id if conversation else None, is_test=is_test)
        dbg["keyword_search"] = kw.debug
    hybrid = await hybrid_search(session, retrieval_text, qvec, top_k=top_k, min_similarity=min_sim)
    kw_hits = kw.hits if kw is not None else []
    usable_kw = kw is not None and kw.usable
    hits = []
    seen_records: set = set()
    for h in (kw_hits + hybrid if usable_kw else hybrid + kw_hits):  # the more confident search first
        rec = (h.document.id, h.chunk.question) if h.chunk.question else ("chunk", h.chunk.id)
        if rec in seen_records:
            continue
        seen_records.add(rec)
        if h.chunk.question and not h.text:  # a Q&A record: its whole answer, not one cut of it
            h.text = await record_text(session, h.chunk)
        hits.append(h)
    hits = hits[:top_k]
    found = qa_hints + [e for e in (kw.extra if kw is not None else []) if e not in qa_hints]
    extra_ctx = program_ctx + value_ctx + [e for e in found if not e.startswith("PROGRAM") and e not in value_ctx]
    chunks = [(h.chunk, h.document, h.score) for h in hits]
    confidence = kw.confidence if usable_kw else (min(1.0, chunks[0][2]) if chunks else 0.0)
    dbg["chunks"] = [{"chunk_id": h.chunk.id, "document": h.document.title, "document_id": h.document.id, "page": h.chunk.page_no,
                      "similarity": h.score, "vector": h.vector, "keywords": f"{h.matched}/{h.total}",
                      "rerank": h.rerank,
                      "question_match": h.question_sim, "preview": h.chunk.content[:160]} for h in hits]
    dbg["retrieval_confidence"] = round(confidence, 4)
    general_answer = bool(await get("general_answer_enabled")) and not knowledge_only
    org_specific = is_org_specific(retrieval_text)
    if not chunks and not found and not program_ctx and (not general_answer or org_specific):
        # nothing in the knowledge base is even close: no AI call (an organisation fact is never answered from outside).
        # With programs on the schedule the AI still reads them: "kothay hobe?" may be about one of them.
        if persist and not is_test:
            await record_unknown(session, text, channel=channel, conversation_id=conversation.id if conversation else None)
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "no_context", debug=dbg))
    if not chunks and not found and not program_ctx:
        dbg["steps"].append("no knowledge match: using enabled general-answer fallback")

    # During a human handover, keep answering only when the stored knowledge itself can produce a
    # high-confidence answer. Never call a generative model or guess while a staff member owns the chat.
    if knowledge_only:
        if pq is not None:  # the schedule itself answers it; no AI while staff own the chat
            return done(PipelineResult(compose_answer(pq, text, today), "program", debug=dbg))
        answer = await extractive_answer(session, retrieval_text, hits, max_chars or 450) if hits else None
        if answer:
            return done(PipelineResult(answer, "kb_extract", debug=dbg))
        if persist and not is_test:
            await record_unknown(session, text, channel=channel, conversation_id=conversation.id if conversation else None)
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "no_context", debug=dbg))

    # ---- 4. minimal guarded prompt -> LLM ---------------------------------------------------------------------------------
    view = await load_profile_view(session, profile) if profile is not None and profile.id is not None else None
    history = history[-hist_len:]
    chunk_budget, system_prompt = int(await get("chunk_token_budget")), (await get("system_prompt")) or DEFAULT_SYSTEM_PROMPT
    system_prompt += await team_instructions.prompt_block(session)  # rules the staff taught in Test Chat
    system_prompt += GENERAL_ANSWER_RULES if general_answer else CONTEXT_CHECK_RULES
    if program_ctx:
        system_prompt += PROGRAM_RULES.format(today=today.isoformat())
    if tone := await tone_for(session, pre_classify(retrieval_text)):  # admin's reply style for this question type
        system_prompt += f"\nTONE for this kind of question: {tone}"
    if max_chars:  # chat replies stay short; the guest can ask for more
        system_prompt += (f"\nKeep the answer short: at most 3 sentences and about {max_chars} characters, "
                          "unless the user explicitly asks for details.")
    # tools (create_lead, request_callback, current_datetime) only for real, persisted conversations; never in eval runs or handover mode
    tool_list = (await enabled_tools(session) if persist and conversation is not None and profile is not None
                 and not knowledge_only else [])
    specs = [t.spec for t in tool_list] or None
    if specs:
        system_prompt += tools_prompt(profile, view)
    conv_summary = conversation.summary if conversation is not None else None
    consent_ok = view is not None and view.consent_state == "given"
    capture: dict = {}

    def build(policy):
        guard = PrivacyGuard(policy, history_len=hist_len, chunk_token_budget=chunk_budget)
        known = (profile.name,) if profile is not None and profile.name else ()
        rd = guard.new_redactor(known_names=known)
        summary = compose_summary(view, rd, policy.never_send) if view else None
        missing = ()  # beyond name/phone/location (lead capture), details are asked only when a request needs them
        capture["summary"], capture["missing"] = summary, missing
        return guard.prepare(PromptInputs(question=asked, profile_summary=summary or None, missing_fields=missing,
                                          recent_messages=tuple(history), conversation_summary=conv_summary,
                                          # short exact values (Q&A, variables, programs) first: they must survive the token budget
                                          chunks=tuple(extra_ctx) + tuple((h.text or h.chunk.content) for h in hits),
                                          system_prompt=system_prompt, reply_language=reply_lang,
                                          final_note=CONTINUE_NOTE if followup_of
                                          else None if general_answer else ANSWER_CHECK_NOTE),
                             redactor=rd)

    large = confidence < float(await get("escalate_below")) or _is_complex(text)
    task = "answer_large" if large else "answer"
    dbg["tier"] = "large" if large else "small"
    dbg["profile_injected"] = {"summary": None, "missing_fields": []}
    try:
        try:
            out = await llm.run(session, task, build, channel=channel, conversation_id=conversation.id if conversation else None,
                                is_test=is_test, tools=specs)
        except LLMUnavailable as exc:
            if task == "answer_large":  # no large route configured / failed: try the standard one
                out = await llm.run(session, "answer", build, channel=channel, conversation_id=conversation.id if conversation else None,
                                    is_test=is_test, tools=specs)
                task = "answer"
            else:
                raise exc
    except LLMUnavailable as exc:
        # no AI model (not configured, failed, or budget stop): answer straight from the best matching text
        dbg["error"] = str(exc)
        if pq is not None:
            return done(PipelineResult(compose_answer(pq, text, today), "program", debug=dbg))
        answer = await extractive_answer(session, retrieval_text, hits, max_chars or 450) if hits else None
        if answer:
            return done(PipelineResult(answer, "kb_extract", debug=dbg))
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "llm_unavailable", debug=dbg))

    own = frozenset(v for v in (consent_svc.get_phone(profile) if profile and profile.phone_enc else None,
                                consent_svc.get_email(profile) if profile and profile.email_enc else None) if v)

    # ---- 4b. tool calls: run / confirm, feed results back (max TOOL_ROUNDS rounds) -------------------------------------
    tool_used, tool_texts = False, []
    if specs:
        tctx = ToolContext(session, profile, conversation, text)
        for _ in range(TOOL_ROUNDS):
            if not out.result.tool_calls:
                break
            tool_used = True
            tr = await run_tool_round(tctx, out.result.tool_calls, out.request, own_values=own,
                                      allowed_values=frozenset(variables.values()))
            dbg.setdefault("tools", []).append({"calls": [c.name for c in out.result.tool_calls],
                                                "awaiting_confirmation": tr.reply is not None})
            if tr.reply is not None:  # the guest must say yes first: ask, run nothing
                return done(PipelineResult(tr.reply, "tool_confirm", out.result.tokens_in, out.result.tokens_out, dbg))
            calls, results = [c for c, _ in tr.results], [r for _, r in tr.results]
            tool_texts = results
            try:
                out = await llm.run(session, task, lambda policy, p=out.request, c=calls, r=results: PrivacyGuard.with_tool_results(p, c, r),
                                    channel=channel, conversation_id=conversation.id, is_test=is_test, tools=specs)
            except LLMUnavailable as exc:  # the action already happened: tell the guest its outcome
                dbg["error"] = str(exc)
                return done(PipelineResult("\n".join(tool_texts), "tool", debug=dbg))
        if out.result.tool_calls:
            dbg["steps"].append(f"tool rounds exhausted ({TOOL_ROUNDS}); remaining calls ignored")
    if tool_used and not out.result.text.strip():
        return done(PipelineResult("\n".join(tool_texts) or await localized(session, "no_answer_reply", text, reply_lang), "tool", debug=dbg))
    if not tool_used and NO_ANSWER.search(out.result.text):
        # the model read every candidate and none answers the question: the verified "don't know" reply
        dbg["steps"].append("AI: none of the retrieved knowledge answers this question")
        dbg.update({"provider": out.result.provider, "model": out.result.model, "task": task,
                    "tokens_in": out.result.tokens_in, "tokens_out": out.result.tokens_out, "cost_usd": float(out.cost_usd)})
        if persist and not is_test:
            await record_unknown(session, text, channel=channel, conversation_id=conversation.id if conversation else None)
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "no_context",
                                   out.result.tokens_in, out.result.tokens_out, dbg))
    chk = check_output(out.result.text.translate(_DEVANAGARI_DIGITS), out.request, own_values=own,
                       allowed_values=frozenset(variables.values()))
    dbg.update({"profile_injected": {"summary": capture.get("summary"), "missing_fields": list(capture.get("missing", ()))},
                "prompt": out.request.audit_payload, "redactions": out.request.report["redactions"],
                "provider": out.result.provider, "model": out.result.model, "task": task,
                "tokens_in": out.result.tokens_in, "tokens_out": out.result.tokens_out, "cost_usd": float(out.cost_usd),
                "fallback_used": out.fallback_used, "budget_fallback": out.budget_fallback,
                "output_blocked": chk.blocked, "output_reasons": chk.reasons, "est_tokens_in": out.request.report["est_tokens_in"]})

    # ---- cache only what is safe to share between users ------------------------------------------------------------------------
    was_blocked = (await session.execute(select(SemanticCache.id).where(  # an admin already marked this exact answer wrong
        SemanticCache.answer == chk.text, SemanticCache.status == "blocked").limit(1))).first() is not None
    if persist and qvec is not None and not chk.blocked and not was_blocked and not tool_used and not out.request.report["redactions"] \
            and not _PLACEHOLDER.search(out.result.text) and not followup_of and not rewrite_used \
            and pq is None and not any(p.name in chk.text for p in programs):  # "ji", "kothay?", "2 days left": here, now
        session.add(SemanticCache(question=text[:500], answer=chk.text, embedding=qvec,
                                  embedding_version=embedding_version, context_version=ctx_version))
        dbg["cached"] = True
    if chk.blocked and persist and not is_test:
        await record_unknown(session, text, channel=channel, conversation_id=conversation.id if conversation else None,
                             reason="output_blocked")
    if chk.blocked:
        # A card/OTP/contact safety block must not look like an answer to the customer's actual question.
        dbg["steps"].append("unsafe model output replaced with verified no-answer reply")
        return done(PipelineResult(await localized(session, "no_answer_reply", text, reply_lang), "safety_fallback",
                                   out.result.tokens_in, out.result.tokens_out, dbg))
    if pq is not None:
        chk.text = quote_program_names(chk.text, pq.programs)
    return done(PipelineResult(chk.text, "llm_large" if task == "answer_large" else "llm", out.result.tokens_in, out.result.tokens_out, dbg))

