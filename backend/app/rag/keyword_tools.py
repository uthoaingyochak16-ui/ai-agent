"""Tools behind the Keywords tab: the keyword table, hand edits, the uniqueness check, AI-suggested keywords (checked
against the record), a live preview for new input, and the index tree."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.models import Chunk, Document, KeywordEntry, Program, QAPair, RecordKeywords, Variable
from app.privacy.guard import PrivacyGuard
from app.rag.keyword_gen import KEYGEN_SYSTEM, find_unique, look_alikes, parse_ai_keywords, stem_of, terms_of
from app.rag.keyword_index import Rec, _load, _topic, reindex_keys, rows_for
from app.rag.keyword_search import _prepare

logger = logging.getLogger(__name__)
TYPES = ("qa", "chunk", "variable", "program", "sheetrow")


# ---- records (titles for the table and the tree) ------------------------------------------------------------------------
async def titles_of(session: AsyncSession, keys) -> dict[tuple[str, str], str]:
    by: dict[str, list[str]] = {}
    for t, r in keys:
        by.setdefault(t, []).append(r)
    out: dict[tuple[str, str], str] = {}
    if by.get("chunk"):
        for i, q, c in (await session.execute(select(Chunk.id, Chunk.question, Chunk.content).where(Chunk.id.in_([int(x) for x in by["chunk"]])))).all():
            out[("chunk", str(i))] = (q or c or "")[:90]
    if by.get("qa"):
        for i, q in (await session.execute(select(QAPair.id, QAPair.question).where(QAPair.id.in_([int(x) for x in by["qa"]])))).all():
            out[("qa", str(i))] = q[:90]
    if by.get("variable"):
        for k, d in (await session.execute(select(Variable.key, Variable.description).where(Variable.key.in_(by["variable"])))).all():
            out[("variable", k)] = f"{k}" + (f" — {d}" if d else "")
    if by.get("program"):
        for i, n in (await session.execute(select(Program.id, Program.name).where(Program.id.in_([int(x) for x in by["program"]])))).all():
            out[("program", str(i))] = n
    if by.get("sheetrow"):
        from app.models import DataRow, DataSheet
        for r, sh in (await session.execute(select(DataRow, DataSheet).join(DataSheet, DataRow.sheet_id == DataSheet.id)
                                            .where(DataRow.id.in_([int(x) for x in by["sheetrow"]])))).all():
            first = (r.values or {}).get((sh.columns or [{}])[0].get("key", ""), "")
            out[("sheetrow", str(r.id))] = f"{sh.name}: {first}"[:90]
    return out


async def list_records(session: AsyncSession, st: str, *, search: str | None, limit: int, offset: int) -> dict:
    s = f"%{search.strip()}%" if search and search.strip() else None
    if st == "qa":
        q = select(QAPair.id, QAPair.question, QAPair.answer_template).where(QAPair.active.is_(True))
        if s:
            q = q.where(or_(QAPair.question.ilike(s), QAPair.answer_template.ilike(s)))
        rows = [(str(i), qq, a[:140]) for i, qq, a in (await session.execute(q.order_by(QAPair.id.desc()).limit(limit).offset(offset))).all()]
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    elif st == "chunk":
        q = select(Chunk.id, Chunk.question, Chunk.content, Document.title).join(Document, Chunk.document_id == Document.id) \
            .where(Document.status == "ready")
        if s:
            q = q.where(or_(Chunk.question.ilike(s), Chunk.content.ilike(s), Document.title.ilike(s)))
        rows = [(str(i), qq or c[:90], f"{d} · {c[:100]}") for i, qq, c, d in
                (await session.execute(q.order_by(Chunk.id).limit(limit).offset(offset))).all()]
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    elif st == "variable":
        q = select(Variable.key, Variable.description, Variable.value)
        if s:
            q = q.where(or_(Variable.key.ilike(s), Variable.description.ilike(s)))
        rows = [(k, k + (f" — {d}" if d else ""), v[:100]) for k, d, v in (await session.execute(q.order_by(Variable.key).limit(limit).offset(offset))).all()]
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    elif st == "sheetrow":
        from app.models import DataRow, DataSheet
        q = select(DataRow.id, DataRow.values, DataSheet.name, DataSheet.columns).join(DataSheet, DataRow.sheet_id == DataSheet.id)
        if s:
            q = q.where(or_(DataRow.search_text.ilike(s.lower()), DataSheet.name.ilike(s)))
        rows = []
        for i, vals, name, cols in (await session.execute(q.order_by(DataSheet.name, DataRow.position, DataRow.id).limit(limit).offset(offset))).all():
            cells = [str((vals or {}).get(c["key"], "")) for c in cols or []]
            rows.append((str(i), f"{name}: {cells[0] if cells else ''}"[:90], " · ".join(x for x in cells[1:] if x)[:120]))
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    else:
        q = select(Program.id, Program.name, Program.place)
        if s:
            q = q.where(or_(Program.name.ilike(s), Program.place.ilike(s)))
        rows = [(str(i), n, p or "") for i, n, p in (await session.execute(q.order_by(Program.start_date).limit(limit).offset(offset))).all()]
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    refs = [r[0] for r in rows]
    entries: dict[str, list] = {r: [] for r in refs}
    ai_at: dict[str, str | None] = {}
    if refs:
        ent = (await session.execute(select(KeywordEntry.source_ref, KeywordEntry.stem, KeywordEntry.term, KeywordEntry.weight, KeywordEntry.origin)
                                     .where(KeywordEntry.source_type == st, KeywordEntry.source_ref.in_(refs)))).all()
        df = dict((await session.execute(select(KeywordEntry.stem, func.count()).where(
            KeywordEntry.stem.in_(sorted({e[1] for e in ent}))).group_by(KeywordEntry.stem))).all()) if ent else {}
        for ref, stem, term, weight, origin in ent:
            entries[ref].append({"term": term, "stem": stem, "weight": weight, "origin": origin, "shared": df.get(stem, 1)})
        ai_at = {r: a for r, a in (await session.execute(select(RecordKeywords.source_ref, RecordKeywords.ai_at).where(
            RecordKeywords.source_type == st, RecordKeywords.source_ref.in_(refs)))).all()}
    items = []
    for ref, title, sub in rows:
        kws = sorted(entries[ref], key=lambda k: (-k["weight"], k["term"]))
        items.append({"type": st, "ref": ref, "title": title, "subtitle": sub, "keywords": kws,
                      "unique": sum(1 for k in kws if k["shared"] == 1 and k["origin"] != "phrase"), "ai_at": ai_at.get(ref)})
    return {"total": total, "items": items}


# ---- hand edits ------------------------------------------------------------------------------------------------------------
async def _row(session: AsyncSession, st: str, ref: str) -> RecordKeywords:
    row = (await session.execute(select(RecordKeywords).where(RecordKeywords.source_type == st, RecordKeywords.source_ref == ref))).scalar_one_or_none()
    if row is None:
        row = RecordKeywords(source_type=st, source_ref=ref, added=[], removed=[])
        session.add(row)
    return row


async def edit_record(session: AsyncSession, st: str, ref: str, *, add: list[str], remove: list[str], restore: list[str]) -> None:
    """Add keywords by hand, switch automatic ones off (by stem), or switch them back on. The flush hook re-indexes."""
    row = await _row(session, st, ref)
    added = list(row.added or [])
    removed = list(row.removed or [])
    have = {stem_of(w) for a in added for w in terms_of(a.get("k", ""))}
    for k in add:
        words = terms_of(k)
        if words and not all(stem_of(w) in have for w in words):
            added.append({"k": k.strip()[:60], "origin": "manual"})
            have |= {stem_of(w) for w in words}
            removed = [s for s in removed if s not in {stem_of(w) for w in words}]
    for stem in remove:
        kept = [a for a in added if stem not in {stem_of(w) for w in terms_of(a.get("k", ""))}]
        if len(kept) == len(added) and stem not in removed:
            removed.append(stem)
        added = kept
    removed = [s for s in removed if s not in set(restore)]
    row.added, row.removed = added, removed


# ---- uniqueness -------------------------------------------------------------------------------------------------------------
async def _neighbors(session: AsyncSession, key: tuple[str, str], topic: set[str]) -> dict:
    """Records that share at least two topic keywords with this one: key -> (topic stems, all stems)."""
    if not topic:
        return {}
    shared = (await session.execute(select(KeywordEntry.source_type, KeywordEntry.source_ref, func.count())
                                    .where(KeywordEntry.stem.in_(sorted(topic)), KeywordEntry.origin.in_(("title", "file", "manual", "ai")),
                                           KeywordEntry.weight >= 2.5)
                                    .group_by(KeywordEntry.source_type, KeywordEntry.source_ref))).all()
    keys = [(t, r) for t, r, c in shared if c >= 2 and (t, r) != key][:60]
    if not keys:
        return {}
    out: dict = {}
    for t in {t for t, _ in keys}:
        refs = [r for tt, r in keys if tt == t]
        for ref, stem, origin, weight in (await session.execute(select(KeywordEntry.source_ref, KeywordEntry.stem, KeywordEntry.origin, KeywordEntry.weight)
                                                                .where(KeywordEntry.source_type == t, KeywordEntry.source_ref.in_(refs)))).all():
            tp, al = out.setdefault((t, ref), (set(), set()))
            if origin != "phrase":
                al.add(stem)
            if origin in ("title", "file", "manual", "ai") and weight >= 2.5:
                tp.add(stem)
    return out


async def make_unique(session: AsyncSession, st: str, ref: str) -> dict:
    """Give this record keywords that none of its look-alikes have (stored as 'unique' keywords)."""
    rec = await session.run_sync(lambda s: _load(s.connection(), st, ref))
    if rec is None:
        return {"unique": [], "look_alikes": []}
    own = (await session.execute(select(KeywordEntry.stem, KeywordEntry.origin, KeywordEntry.weight).where(
        KeywordEntry.source_type == st, KeywordEntry.source_ref == ref))).all()
    topic = {s for s, o, w in own if o in ("title", "file", "manual", "ai") and w >= 2.5}
    neighbors = await _neighbors(session, (st, ref), topic)
    alike = look_alikes(topic, neighbors)
    words = find_unique(Counter(terms_of(rec.text)), set().union(*(neighbors[k][1] for k in alike))) if alike else []
    row = await _row(session, st, ref)
    row.added = [a for a in (row.added or []) if a.get("origin") != "unique"] + [{"k": w, "origin": "unique"} for w in words]
    titles = await titles_of(session, alike)
    return {"unique": words, "look_alikes": [titles.get(k, "/".join(k)) for k in alike]}


# ---- AI suggestions ----------------------------------------------------------------------------------------------------------
async def ai_generate(session: AsyncSession, llm: LLMService, st: str, ref: str) -> dict:
    """Ask the model for this record's keywords; keep only those grounded in the record. Raises LLMUnavailable."""
    await _prepare(session)    # teaches the Banglish/synonym matcher the index vocabulary, used by the grounding check
    rec: Rec | None = await session.run_sync(lambda s: _load(s.connection(), st, ref))
    if rec is None:
        return {"kept": [], "rejected": []}
    own = (await session.execute(select(KeywordEntry.stem, KeywordEntry.origin, KeywordEntry.weight).where(
        KeywordEntry.source_type == st, KeywordEntry.source_ref == ref))).all()
    neighbors = await _neighbors(session, (st, ref), {s for s, o, w in own if o in ("title", "file", "manual", "ai") and w >= 2.5})
    similar = list((await titles_of(session, list(neighbors)[:5])).values())
    content = (f"RECORD QUESTION: {rec.title or '-'}\nRECORD TEXT: {(rec.body or '')[:1400]}\nSIMILAR RECORDS (questions):\n"
               + ("\n".join(f"- {t}" for t in similar) or "- (none)"))

    def build(policy):
        return PrivacyGuard(policy).prepare_task(KEYGEN_SYSTEM, content)
    out = await llm.run(session, "keygen", build, channel=None, conversation_id=None, is_test=True)
    kept, rejected = parse_ai_keywords(out.result.text, rec.text)
    row = await _row(session, st, ref)
    row.added = [a for a in (row.added or []) if a.get("origin") != "ai"] + [{"k": k, "origin": "ai"} for k in kept]
    row.ai_at = datetime.now(timezone.utc)
    return {"kept": kept, "rejected": rejected, "tokens": out.result.tokens_in + out.result.tokens_out}


# ---- preview for new input -----------------------------------------------------------------------------------------------------
async def preview(session: AsyncSession, title: str, body: str, category: str | None = None) -> list[dict]:
    """The keywords a new Q&A / text would get, with how many existing records already share each one."""
    rec = Rec("qa", "new", None, title=title, meta=category, body=body)
    n = (await session.execute(select(func.count()).select_from(select(KeywordEntry.source_type, KeywordEntry.source_ref).distinct().subquery()))).scalar_one()
    stems = sorted({stem_of(w) for w in terms_of(rec.text)})
    df = dict((await session.execute(select(KeywordEntry.stem, func.count()).where(KeywordEntry.stem.in_(stems)).group_by(KeywordEntry.stem))).all()) if stems else {}
    rows = rows_for(rec, {s: df.get(s, 0) for s in stems}, n + 1)
    rows.sort(key=lambda r: -r["weight"])
    return [{"term": r["term"], "origin": r["origin"], "weight": r["weight"], "shared": df.get(r["stem"], 0)} for r in rows]


# ---- the tree ------------------------------------------------------------------------------------------------------------------
async def keyword_tree(session: AsyncSession, *, roots: int = 24, children: int = 8, depth: int = 3, leaves: int = 10) -> dict:
    """Keywords as a tree: the most shared keywords first; under each, the keywords its records also have, narrowing
    down until only a few records are left. A record appears under every keyword it has."""
    ent = (await session.execute(select(KeywordEntry.stem, KeywordEntry.term, KeywordEntry.source_type, KeywordEntry.source_ref)
                                 .where(KeywordEntry.origin != "phrase"))).all()
    inv: dict[str, set] = {}
    stems_of: dict[tuple, set] = {}
    surface: dict[str, Counter] = {}
    for stem, term, st, ref in ent:
        key = (st, ref)
        inv.setdefault(stem, set()).add(key)
        stems_of.setdefault(key, set()).add(stem)
        surface.setdefault(stem, Counter())[term] += 1
    n = len(stems_of)
    label = lambda s: surface[s].most_common(1)[0][0]  # noqa: E731
    wanted: set = set()

    def build(stem: str, subset: set, ancestors: frozenset, level: int) -> dict:
        node = {"term": label(stem), "stem": stem, "count": len(subset), "children": [], "records": []}
        if level > 1 and len(subset) > 2:
            counts: Counter = Counter()
            for k in subset:
                counts.update(stems_of[k] - ancestors - {stem})
            nxt = [(s, c) for s, c in counts.most_common() if 2 <= c < len(subset)][:children]
            for s, _ in nxt:
                node["children"].append(build(s, subset & inv[s], ancestors | {stem}, level - 1))
        if not node["children"]:
            node["records"] = sorted(subset)[:leaves]
            wanted.update(node["records"])
        return node

    ranked = [(s, len(k)) for s, k in inv.items() if 2 <= len(k) <= max(3, int(0.6 * n))]
    ranked.sort(key=lambda sc: (-sc[1], sc[0]))
    tree = [build(s, inv[s], frozenset(), depth) for s, _ in ranked[:roots]]
    titles = await titles_of(session, wanted)

    def fill(node: dict) -> None:
        node["records"] = [{"type": t, "ref": r, "title": titles.get((t, r), f"{t} {r}")} for t, r in node["records"]]
        node["more"] = max(0, node["count"] - len(node["records"])) if node["records"] else 0
        for c in node["children"]:
            fill(c)
    for node in tree:
        fill(node)
    return {"records": n, "keywords": len(inv), "roots": tree}


async def look_alike_report(session: AsyncSession, limit: int = 30) -> list[dict]:
    """Records that still share most of their topic keywords with another, and what separates them."""
    from app.rag.keyword_index import _load_all, find_look_alike_groups
    recs = await _load_all(session)
    rows: dict = {k: [] for k in recs}
    for stem, term, st, ref, origin, weight in (await session.execute(select(
            KeywordEntry.stem, KeywordEntry.term, KeywordEntry.source_type, KeywordEntry.source_ref, KeywordEntry.origin, KeywordEntry.weight))).all():
        if (st, ref) in rows:
            rows[(st, ref)].append({"stem": stem, "term": term, "origin": origin, "weight": weight})
    groups = find_look_alike_groups(recs, rows)
    keys = set(groups) | {k for _, a in groups.values() for k in a}
    titles = await titles_of(session, list(keys)[:300])
    out = []
    for k, (words, alike) in sorted(groups.items(), key=lambda kv: -len(kv[1][1]))[:limit]:
        has_unique = any(r["origin"] == "unique" for r in rows[k])
        out.append({"type": k[0], "ref": k[1], "title": titles.get(k, "/".join(k)), "unique": [r["term"] for r in rows[k] if r["origin"] == "unique"] or words,
                    "look_alikes": [titles.get(a, "/".join(a)) for a in alike[:5]], "separated": has_unique})
    return out


async def pending_ai_records(session: AsyncSession, types: list[str], limit: int) -> list[tuple[str, str]]:
    """Records the AI has not written keywords for yet."""
    from sqlalchemy import and_
    todo: list[tuple[str, str]] = []

    def unkeyed(st: str, ref_col):
        return and_(or_(RecordKeywords.id.is_(None), RecordKeywords.ai_at.is_(None)))

    for st in types:
        if len(todo) >= limit:
            break
        left = limit - len(todo)
        join = lambda ref_col: and_(RecordKeywords.source_type == st, RecordKeywords.source_ref == ref_col)  # noqa: E731
        if st == "qa":
            q = select(QAPair.id).outerjoin(RecordKeywords, join(cast(QAPair.id, String))).where(QAPair.active.is_(True), unkeyed(st, None)).order_by(QAPair.id)
        elif st == "chunk":
            q = select(Chunk.id).join(Document, Chunk.document_id == Document.id).outerjoin(RecordKeywords, join(cast(Chunk.id, String)))                 .where(Document.status == "ready", unkeyed(st, None)).order_by(Chunk.id)
        elif st == "variable":
            q = select(Variable.key).outerjoin(RecordKeywords, join(Variable.key)).where(unkeyed(st, None)).order_by(Variable.key)
        elif st == "sheetrow":
            from app.models import DataRow
            q = select(DataRow.id).outerjoin(RecordKeywords, join(cast(DataRow.id, String))).where(unkeyed(st, None)).order_by(DataRow.id)
        else:
            q = select(Program.id).outerjoin(RecordKeywords, join(cast(Program.id, String))).where(unkeyed(st, None)).order_by(Program.id)
        todo += [(st, str(r)) for r in (await session.execute(q.limit(left))).scalars()]
    return todo
