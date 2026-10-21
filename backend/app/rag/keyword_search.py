"""Answering through the keyword index (rag/keyword_index.py):

  1. keywords   the guest's question -> a few search keywords. Free rule-based extraction first; if that already hits the
                index convincingly, stop (no tokens). Otherwise one small AI call ("keywords" route) works out what the
                guest wants and gives the keywords in Bangla and English.
  2. search     each keyword becomes a group of variants (spelling / inflection stems, English <-> Bangla synonyms,
                Banglish transliterations); the index rows of those stems are the candidate records. A record scores by
                the rarity (IDF) of the keywords it covers and how strongly it is tagged with them (title > keywords >
                body), and must cover enough of the question's meaning.
  3. details    only the winning records are loaded (a Q&A record's chunks are joined into its full answer), and only
                they go to the answer step: a short, precise context instead of a similarity scan over every chunk.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.models import Chunk, Document, KeywordEntry, Program, QAPair, Variable
from app.privacy.guard import PrivacyGuard
from app.rag import search as S
from app.rag.keyword_gen import phrase_stems
from app.rag.keyword_index import W_TITLE, stem_of, terms_of
from app.rag.programs import context_line, today_bd
from app.rag.templates import fill_known, fill_template
from app.rag.translit import build_keymap
from app.rag.variables import NEUTRAL_WORDS, variable_answer
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
MAX_KEYWORDS = 8
CANDIDATES = 30
MAX_RECORD_WORDS = 220
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")

KEYWORD_SYSTEM = """You prepare a lookup in an organisation's keyword index. Customers write Bangla, English or Banglish
(Bangla in Latin letters). Return ONLY a JSON object:
{"intent": "<what the customer wants, 3-8 English words>", "keywords": ["...", "..."]}
Keywords (at most 8): the topic words and entities, plus the kind of information asked (fee, date, place, how to join,
benefits, meaning). No question words, no filler, no personal data. Give each important concept in BOTH Bangla script
and English (for example "ফি" and "fee"). Resolve words like "it" / "oita" from the recent turns. Never answer the question."""


_KINDS = (("amount", {"koto", "kto", "কত", "কতো", "much", "price", "cost"}),
          ("how", {"kivabe", "kibhabe", "kibhave", "kemne", "কিভাবে", "কীভাবে", "কেমনে", "how"}),
          ("where", {"kothay", "kothai", "kothae", "kuthay", "kotai", "কোথায়", "কোথায়", "where"}), ("when", {"kobe", "kokhon", "কবে", "কখন", "when"}),
          ("who", {"কারা", "কার", "who", "whom"}), ("why", {"keno", "কেন", "why"}))


def question_kind(text: str) -> str | None:
    """What sort of answer is asked for: an amount, a way, a place, a time, a person, a reason (None = unclear)."""
    tokens = set(S.norm(text).split())
    return next((kind for kind, words in _KINDS if tokens & words), None)


# ---- plans -----------------------------------------------------------------------------------------------------------
@dataclass
class KeywordPlan:
    keywords: list[str]
    source: str                 # "rules" | "ai"
    intent: str | None = None


def rules_plan(text: str) -> KeywordPlan:
    return KeywordPlan(list(dict.fromkeys(S.content_words(text)))[:MAX_KEYWORDS * 2], "rules")


def parse_plan(raw: str) -> KeywordPlan | None:
    d = parse_json_object(raw or "")
    if not d or not isinstance(d.get("keywords"), list):
        return None
    kws: list[str] = []
    for k in d["keywords"]:
        if isinstance(k, str) and 1 < len(k.strip()) <= 60 and not _PLACEHOLDER.search(k):
            kws.append(k.strip())
    if not kws:
        return None
    intent = d.get("intent") if isinstance(d.get("intent"), str) else None
    return KeywordPlan(kws[:MAX_KEYWORDS], "ai", intent[:120] if intent else None)


async def ai_plan(session: AsyncSession, llm: LLMService, *, text: str, history: list[tuple[str, str]], channel: str | None,
                  conversation_id: int | None, is_test: bool) -> tuple[KeywordPlan | None, str | None]:
    """(plan, note). One small call through PrivacyGuard; any failure returns (None, why) and the rules keywords are used."""
    turns = "\n".join(f"{'CUSTOMER' if r == 'user' else 'ASSISTANT'}: {t[:300]}" for r, t in history[-2:]) \
        if history and len(text.split()) <= 8 else ""
    content = (f"RECENT TURNS:\n{turns}\n" if turns else "") + f"MESSAGE:\n{text}"

    def build(policy):
        return PrivacyGuard(policy).prepare_task(KEYWORD_SYSTEM, content)
    try:
        out = await llm.run(session, "keywords", build, channel=channel, conversation_id=conversation_id, is_test=is_test)
    except LLMUnavailable as exc:
        return None, f"AI keywords unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 - an optimisation: the reply must never depend on it
        logger.warning("keyword extraction failed: %s", exc)
        return None, "AI keywords failed"
    plan = parse_plan(out.result.text)
    return (plan, None) if plan else (None, "AI keywords unreadable")


# ---- the index ---------------------------------------------------------------------------------------------------------
_CACHE: dict = {"sig": None, "n": 0}


def forget_cache() -> None:
    _CACHE["sig"] = None


async def _prepare(session: AsyncSession) -> int:
    """Number of indexed records. Also teaches the Banglish -> Bangla matcher the index's own vocabulary."""
    count, last = (await session.execute(select(func.count(), func.coalesce(func.max(KeywordEntry.id), 0)))).one()
    if _CACHE["sig"] != (count, last):
        words = [t for t, _ in (await session.execute(select(KeywordEntry.term, func.count()).where(KeywordEntry.origin != "phrase")
                                                      .group_by(KeywordEntry.term).order_by(func.count().desc()).limit(20000))).all()]
        S._KEYMAP.clear()
        S._KEYMAP.update(build_keymap(words))
        _CACHE["n"] = (await session.execute(select(func.count()).select_from(
            select(KeywordEntry.source_type, KeywordEntry.source_ref).distinct().subquery()))).scalar_one()
        _CACHE["sig"] = (count, last)
    return _CACHE["n"]


@dataclass
class Match:
    source_type: str
    ref: str
    document_id: int | None
    score: float            # blend of coverage and tag strength, 0..1
    coverage: float         # share of the question's keyword rarity this record covers
    matched: int
    groups: frozenset[int] = frozenset()  # which of the question's concepts it covers


@dataclass
class IndexSearch:
    labels: list[str]
    matches: list[Match] = field(default_factory=list)
    anchor: int | None = None  # the question's most distinctive concept that some record has ("bandarban", not "office")

    @property
    def top_coverage(self) -> float:
        return max((m.coverage for m in self.matches), default=0.0)


def _groups(keywords: list[str]) -> list[tuple[str, set[str]]]:
    """One group per concept: a keyword's variants and their stems; groups that share a stem are the same concept
    ("fee" and "ফি") and are merged so it is not counted twice."""
    groups: list[tuple[list[str], set[str]]] = []
    seen: set[str] = set()
    for k in keywords:
        for w in terms_of(k):
            if w in seen:
                continue
            seen.add(w)
            stems = {stem_of(a) for a in S._alternatives(w) if a} | {stem_of(w)}
            for labels, existing in groups:
                if existing & stems:
                    labels.append(w)
                    existing |= stems
                    break
            else:
                groups.append(([w], stems))
    return [("/".join(labels[:3]), stems) for labels, stems in groups]


async def search_index(session: AsyncSession, keywords: list[str], *, limit: int = CANDIDATES,
                       phrases: list[str] = ()) -> IndexSearch:
    """`phrases`: adjacent word pairs of the question ("course_fee"). A record tagged with the same pair gets a small
    bonus, which separates "course fee" records from "course registration" ones."""
    n = await _prepare(session)
    groups = _groups(keywords)
    out = IndexSearch([g[0] for g in groups])
    if not groups or n == 0:
        return out
    all_stems = set().union(*(s for _, s in groups))
    phrase_set = set(phrases)
    rows = (await session.execute(select(KeywordEntry.stem, KeywordEntry.source_type, KeywordEntry.source_ref,
                                         KeywordEntry.document_id, KeywordEntry.weight)
                                  .where(KeywordEntry.stem.in_(sorted(all_stems | phrase_set))))).all()
    by_source: dict[tuple[str, str], dict[int, float]] = {}
    doc_of: dict[tuple[str, str], int | None] = {}
    phrase_hits: dict[tuple[str, str], int] = {}
    for stem, st, ref, doc_id, weight in rows:
        if stem in phrase_set:
            phrase_hits[(st, ref)] = phrase_hits.get((st, ref), 0) + 1
            by_source.setdefault((st, ref), {})
            doc_of[(st, ref)] = doc_id
            continue
        for gi, (_, stems) in enumerate(groups):
            if stem in stems:
                slot = by_source.setdefault((st, ref), {})
                slot[gi] = max(slot.get(gi, 0.0), weight)
                doc_of[(st, ref)] = doc_id
    df = [sum(1 for g in by_source.values() if gi in g) for gi in range(len(groups))]
    idf = [math.log(1 + n / d) if d else math.log(1 + n) for d in df]      # a keyword nothing has is as telling as the rarest one
    total = sum(idf) or 1.0
    known = [gi for gi in range(len(groups)) if df[gi]]
    out.anchor = max(known, key=lambda gi: idf[gi]) if known else None
    for (st, ref), g in by_source.items():
        covered = sum(idf[gi] for gi in g)
        strength = sum(idf[gi] * w for gi, w in g.items()) / (total * W_TITLE)
        if not g:   # matched only a phrase: not a candidate by itself
            continue
        bonus = min(0.12, 0.06 * phrase_hits.get((st, ref), 0))
        out.matches.append(Match(st, ref, doc_of[(st, ref)], min(1.0, 0.65 * (covered / total) + 0.35 * min(1.0, strength) + bonus),
                                 covered / total, len(g), frozenset(g)))
    out.matches.sort(key=lambda m: (-m.score, -m.coverage))
    del out.matches[limit:]
    return out


# ---- details -------------------------------------------------------------------------------------------------------------
@dataclass
class KeywordResult:
    hits: list[S.Hit]
    extra: list[str]            # Q&A / variable / program records, ready for the prompt
    confidence: float
    usable: bool
    debug: dict
    anchored: bool = False  # the best record holds the question's most distinctive keyword (the place / program named)


async def _record_text(session: AsyncSession, c: Chunk) -> str:
    """A Q&A-shaped chunk -> its whole answer (all chunks of the record), otherwise the chunk itself."""
    if not c.question:
        return c.content
    parts = (await session.execute(select(Chunk.content).where(Chunk.document_id == c.document_id, Chunk.question == c.question)
                                   .order_by(Chunk.position))).scalars().all()
    merged = " ".join(S._dedupe_overlap([S.strip_head(p) for p in parts]).split()[:MAX_RECORD_WORDS])
    q_label, a_label = ("প্রশ্ন", "উত্তর") if any("ঀ" <= ch <= "৿" for ch in c.question) else ("Question", "Answer")
    return f"{q_label}: {c.question}\n{a_label}: {merged}"


async def keyword_retrieve(session: AsyncSession, llm: LLMService, *, text: str, retrieval_text: str,
                           history: list[tuple[str, str]], channel: str | None, conversation_id: int | None,
                           is_test: bool = False, allow_ai: bool = True) -> KeywordResult:
    t0 = time.perf_counter()
    dbg: dict = {"used": False}
    n = await _prepare(session)
    if n == 0:
        dbg["reason"] = "the keyword index is empty"
        return KeywordResult([], [], 0.0, False, dbg)
    min_cov = float(await get_setting(session, "keyword_min_coverage") or 0.5)
    top_k = max(1, int(await get_setting(session, "keyword_top_k") or 4))
    skip_above = float(await get_setting(session, "keyword_ai_skip_above") or 0)

    plan = rules_plan(retrieval_text)
    phrases = phrase_stems(S.content_words(retrieval_text))
    res = await search_index(session, plan.keywords, phrases=phrases)
    dbg["rules_keywords"], dbg["rules_coverage"] = res.labels, round(res.top_coverage, 3)
    if allow_ai and await get_setting(session, "keyword_ai_enabled") and (not res.matches or res.top_coverage < skip_above or skip_above <= 0):
        ai, note = await ai_plan(session, llm, text=text, history=history, channel=channel, conversation_id=conversation_id,
                                 is_test=is_test)
        if ai is not None:
            res_ai = await search_index(session, ai.keywords, phrases=phrases)
            if res_ai.matches or not res.matches:
                plan, res = ai, res_ai
        if note:
            dbg["ai_note"] = note
    dbg.update({"source": plan.source, "intent": plan.intent, "keywords": res.labels, "coverage": round(res.top_coverage, 3)})

    floor = max(min_cov, 0.6 * res.top_coverage)
    cands = [m for m in res.matches if m.coverage >= floor]
    chunk_ids = [int(m.ref) for m in cands if m.source_type == "chunk"]
    chunks = {c.id: (c, d) for c, d in (await session.execute(
        select(Chunk, Document).join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(chunk_ids), Document.status == "ready"))).all()} if chunk_ids else {}
    qas = {q.id: q for q in (await session.execute(select(QAPair).where(
        QAPair.id.in_([int(m.ref) for m in cands if m.source_type == "qa"]), QAPair.active.is_(True)))).scalars()}
    vrs = {v.key: v for v in (await session.execute(select(Variable).where(
        Variable.key.in_([m.ref for m in cands if m.source_type == "variable"])))).scalars()}
    today = today_bd()
    progs = {p.id: p for p in (await session.execute(select(Program).where(
        Program.id.in_([int(m.ref) for m in cands if m.source_type == "program"])))).scalars() if p.last_day >= today}
    variables = {v.key: v.value for v in (await session.execute(select(Variable))).scalars()} if qas else {}
    from app.models import DataRow, DataSheet
    from app.services.sheets import row_line
    sheet_rows = {r.id: (r, sh) for r, sh in (await session.execute(
        select(DataRow, DataSheet).join(DataSheet, DataRow.sheet_id == DataSheet.id).where(
            DataRow.id.in_([int(m.ref) for m in cands if m.source_type == "sheetrow"]), DataSheet.active.is_(True)))).all()}

    # Tie-breaker: of two records covering the same keywords, the one whose own question is mostly about what was asked
    # wins ("কোর্সে রেজিস্ট্রেশন করব কীভাবে?" over a record that merely also mentions the course)
    asked_kind = question_kind(retrieval_text)
    for m in cands:
        got = chunks.get(int(m.ref)) if m.source_type == "chunk" else None
        words = [w for w in S.content_words(got[0].question) if w not in NEUTRAL_WORDS] if got and got[0].question else []
        if words:
            matched, total = S.coverage(words, retrieval_text)
            m.score = round(0.85 * m.score + 0.15 * (matched / total), 4)
            kind = question_kind(got[0].question)
            if asked_kind and kind:   # "ফি কত?" (how much) beats "ফি কীভাবে দিতে হয়?" (how) for a how-much question
                m.score = round(m.score + (0.08 if kind == asked_kind else -0.04), 4)
    cands.sort(key=lambda m: (-m.score, -m.coverage))

    hits: list[S.Hit] = []
    extra: list[str] = []
    seen_records: set = set()
    picked: list[dict] = []
    best: Match | None = None
    for m in cands:
        if len(picked) >= top_k:
            break
        if m.source_type == "chunk":
            got = chunks.get(int(m.ref))
            if not got:
                continue
            c, d = got
            rec = ("record", c.document_id, c.question) if c.question else ("chunk", c.id)
            if rec in seen_records:
                continue
            seen_records.add(rec)
            hits.append(S.Hit(c, d, round(m.score, 4), 0.0, 0.0, m.matched, len(res.labels),
                              round(S.question_similarity(retrieval_text, c.question), 3), round(m.coverage, 3),
                              text=await _record_text(session, c)))
        elif m.source_type == "qa" and int(m.ref) in qas:
            q = qas[int(m.ref)]
            filled, missing = fill_template(q.answer_template, variables)
            if missing:  # some values not given yet: the rest of the answer still helps the AI
                filled = fill_known(q.answer_template, variables)
                if not filled:
                    continue
            extra.append(f"Q&A — {q.question}\n{filled}")
        elif m.source_type == "variable" and m.ref in vrs:
            extra.append("VALUE — " + variable_answer(vrs[m.ref]))
        elif m.source_type == "program" and int(m.ref) in progs:
            extra.append(context_line(progs[int(m.ref)], today))
        elif m.source_type == "sheetrow" and int(m.ref) in sheet_rows:
            r, sh = sheet_rows[int(m.ref)]
            extra.append(row_line(sh, r.values or {}))
        else:
            continue
        picked.append({"type": m.source_type, "ref": m.ref, "score": round(m.score, 3), "coverage": round(m.coverage, 3)})
        best = best or m
    usable = bool(picked) and res.top_coverage >= min_cov
    anchored = usable and best is not None and res.anchor is not None and res.anchor in best.groups
    dbg.update({"used": usable, "anchored": anchored, "records": picked, "ms": int((time.perf_counter() - t0) * 1000)})
    return KeywordResult(hits, extra, round(max((p["score"] for p in picked), default=0.0), 4), usable, dbg, anchored)
