"""The keyword index: every record the assistant can answer from, described by a set of weighted keywords.

Structure (table keyword_entries, one row per keyword stem and record; `origin` says where a keyword came from):

    record               what is indexed (weight)
    -------------------  --------------------------------------------------------------------------------------
    chunk (file text)    question (3.0 title) · topic / keywords written in the file (2.5 file) · document title
                         (1.5 doc) · the most distinctive words of the text (1.0 body, more for longer text)
                         · its rarest words (2.0 signature) · adjacent question words (3.5 phrase)
    qa (Q&A pair)        question · category · answer words, the same way
    variable             name and description          program   name · place · fee, notes, dates
    sheetrow             a data-sheet row: first column (title) · sheet name · sheet description · other cells

On top of the automatic keywords: keywords added by hand / by the AI (2.8-3.0, kept in record_keywords), and "unique"
keywords the uniqueness check gives to records that would otherwise look alike. A keyword is stored as a stem
(inflections removed: প্রেম / প্রেমের / প্রেমে), so a search needs no scan of the texts. Searching: rag/keyword_search.py;
generating: rag/keyword_gen.py.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Chunk, DataRow, DataSheet, Document, KeywordEntry, Program, QAPair, RecordKeywords, Variable
from app.rag.keyword_gen import (W_BODY, W_DOC, W_META, W_TITLE, W_BY_ORIGIN, Kw, extra_keywords, find_unique,  # noqa: F401
                                 generate_keywords, look_alikes, meta_terms, stem_of, terms_of)

logger = logging.getLogger(__name__)
BATCH = 400
IN_LIMIT = 4000


@dataclass
class Rec:
    type: str
    ref: str
    document_id: int | None = None
    title: str | None = None
    meta: str | None = None
    doc: str | None = None
    body: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.type, self.ref

    @property
    def text(self) -> str:
        return " ".join(x for x in (self.title, self.meta, self.doc, self.body) if x)


def rec_chunk(c, doc_title: str | None) -> Rec:
    return Rec("chunk", str(c.id), c.document_id, title=c.question, meta=c.keywords, doc=doc_title, body=c.content)


def rec_qa(q) -> Rec:
    return Rec("qa", str(q.id), None, title=q.question, meta=q.category, body=q.answer_template)


def rec_variable(v) -> Rec:
    return Rec("variable", v.key, None, title=f"{v.key} {v.description or ''}")


def rec_program(p) -> Rec:
    extra = " ".join(x for x in (getattr(p, "fee", None), p.notes, getattr(p, "date_text", None)) if x)
    return Rec("program", str(p.id), None, title=p.name, meta=p.place, body=extra)


def rec_sheetrow(r, sheet) -> Rec:
    from app.services.sheets import index_parts
    return Rec("sheetrow", str(r.id), None, **index_parts(sheet, r.values or {}))


def _ref(o) -> tuple[str, str]:
    if isinstance(o, Chunk):
        return "chunk", str(o.id)
    if isinstance(o, QAPair):
        return "qa", str(o.id)
    if isinstance(o, Variable):
        return "variable", o.key
    if isinstance(o, DataRow):
        return "sheetrow", str(o.id)
    return "program", str(o.id)


def rows_for(rec: Rec, df: dict | None = None, n: int = 0, added: list | None = None, removed: list | None = None) -> list[dict]:
    """The index rows of one record: automatic keywords + added ones - removed ones."""
    by = {k.stem: k for k in generate_keywords(title=rec.title, meta=rec.meta, doc=rec.doc, body=rec.body, df=df, n=n)}
    for k in extra_keywords(added):
        if k.stem not in by or by[k.stem].weight < k.weight:
            by[k.stem] = k
    for s in removed or ():
        by.pop(s, None)
    return [{"stem": k.stem, "term": k.term, "source_type": rec.type, "source_ref": rec.ref, "document_id": rec.document_id,
             "weight": k.weight, "origin": k.origin} for k in by.values()]


def build_rows(source_type: str, ref: str, document_id: int | None, *, title: str | None = None, meta: str | None = None,
               doc: str | None = None, body: str | None = None) -> list[dict]:
    rows = rows_for(Rec(source_type, ref, document_id, title, meta, doc, body))
    rows.sort(key=lambda r: -r["weight"])
    return rows


def _insert(conn, rows: list[dict]) -> None:
    for i in range(0, len(rows), 2000):
        conn.execute(pg_insert(KeywordEntry).on_conflict_do_nothing(constraint="uq_kw_source_stem"), rows[i:i + 2000])


# ---- incremental updates (called from the flush hook, with a synchronous connection) ---------------------------------
def _load(conn, st: str, ref: str) -> Rec | None:
    """A record by key, for when only its keyword edits changed."""
    if st == "chunk":
        r = conn.execute(select(Chunk.id, Chunk.document_id, Chunk.question, Chunk.keywords, Chunk.content, Document.title)
                         .join(Document, Chunk.document_id == Document.id).where(Chunk.id == int(ref))).first()
        return rec_chunk(SimpleNamespace(id=r[0], document_id=r[1], question=r[2], keywords=r[3], content=r[4]), r[5]) if r else None
    if st == "qa":
        r = conn.execute(select(QAPair.id, QAPair.question, QAPair.category, QAPair.answer_template)
                         .where(QAPair.id == int(ref), QAPair.active.is_(True))).first()
        return rec_qa(SimpleNamespace(id=r[0], question=r[1], category=r[2], answer_template=r[3])) if r else None
    if st == "variable":
        r = conn.execute(select(Variable.key, Variable.description).where(Variable.key == ref)).first()
        return rec_variable(SimpleNamespace(key=r[0], description=r[1])) if r else None
    if st == "sheetrow":
        r = conn.execute(select(DataRow.id, DataRow.values, DataSheet.name, DataSheet.description, DataSheet.columns)
                         .join(DataSheet, DataRow.sheet_id == DataSheet.id)
                         .where(DataRow.id == int(ref), DataSheet.active.is_(True))).first()
        return rec_sheetrow(SimpleNamespace(id=r[0], values=r[1]),
                            SimpleNamespace(name=r[2], description=r[3], columns=r[4])) if r else None
    cols = [Program.id, Program.name, Program.place, Program.notes] + [getattr(Program, c) for c in ("fee", "date_text") if hasattr(Program, c)]
    r = conn.execute(select(*cols).where(Program.id == int(ref))).first()
    return rec_program(SimpleNamespace(**dict(zip([c.key for c in cols], r)))) if r else None


def _extras(conn, keys) -> dict[tuple[str, str], tuple[list, list]]:
    by_type: dict[str, list[str]] = {}
    for t, r in keys:
        by_type.setdefault(t, []).append(r)
    if not by_type:
        return {}
    cond = or_(*[and_(RecordKeywords.source_type == t, RecordKeywords.source_ref.in_(refs)) for t, refs in by_type.items()])
    return {(x.source_type, x.source_ref): (list(x.added or []), list(x.removed or []))
            for x in conn.execute(select(RecordKeywords.source_type, RecordKeywords.source_ref, RecordKeywords.added,
                                         RecordKeywords.removed).where(cond)).all()}


def _df_context(conn, recs: list[Rec]) -> tuple[dict[str, int], int]:
    """How many records contain each stem (index + this batch) and how many records there are."""
    batch: Counter = Counter()
    for r in recs:
        batch.update({stem_of(w) for w in terms_of(r.text)})
    db: dict[str, int] = {}
    stems = sorted(batch)
    for i in range(0, len(stems), IN_LIMIT):
        db.update(dict(conn.execute(select(KeywordEntry.stem, func.count()).where(KeywordEntry.stem.in_(stems[i:i + IN_LIMIT]))
                                    .group_by(KeywordEntry.stem)).all()))
    n = conn.execute(select(func.count()).select_from(select(KeywordEntry.source_type, KeywordEntry.source_ref).distinct().subquery())).scalar_one()
    return {s: db.get(s, 0) + batch[s] for s in stems}, n + len(recs)


def apply_changes(conn, changed: list, removed: list, docs_removed: list) -> None:
    recs: dict[tuple[str, str], Rec] = {}
    touched: set[tuple[str, str]] = set()
    chunk_docs = {c.document_id for c in changed if isinstance(c, Chunk)}
    titles = dict(conn.execute(select(Document.id, Document.title).where(Document.id.in_(chunk_docs))).all()) if chunk_docs else {}
    for o in changed:
        if isinstance(o, RecordKeywords):
            touched.add((o.source_type, o.source_ref))
        elif isinstance(o, Chunk):
            recs[_ref(o)] = rec_chunk(o, titles.get(o.document_id))
        elif isinstance(o, QAPair):
            touched.add(_ref(o))
            if o.active:
                recs[_ref(o)] = rec_qa(o)
        elif isinstance(o, Variable):
            recs[_ref(o)] = rec_variable(o)
        elif isinstance(o, Program):
            recs[_ref(o)] = rec_program(o)
        elif isinstance(o, DataRow):
            touched.add(_ref(o))           # loaded below with its sheet (an inactive sheet's rows leave the index)
        elif isinstance(o, DataSheet):     # renamed / described / columns / switched on or off: all its rows again
            touched |= {("sheetrow", str(i)) for i in conn.execute(select(DataRow.id).where(DataRow.sheet_id == o.id)).scalars()}
    for key in touched - set(recs):          # only its keyword edits changed: load the record itself
        rec = _load(conn, *key)
        if rec is not None:
            recs[key] = rec
    gone = {_ref(o) for o in removed if not isinstance(o, (RecordKeywords, DataSheet))} | touched | set(recs)
    by_type: dict[str, set[str]] = {}
    for t, r in gone:
        by_type.setdefault(t, set()).add(r)
    for t, refs in by_type.items():
        conn.execute(delete(KeywordEntry).where(KeywordEntry.source_type == t, KeywordEntry.source_ref.in_(sorted(refs))))
    if docs_removed:
        conn.execute(delete(KeywordEntry).where(KeywordEntry.document_id.in_([d.id for d in docs_removed])))
    _write(conn, recs)


def reindex_keys(conn, keys: set[tuple[str, str]]) -> None:
    """Rebuild the index rows of these records from their current data and keyword edits."""
    recs = {k: r for k in keys if (r := _load(conn, *k)) is not None}
    for t, refs in _group(set(keys)).items():
        conn.execute(delete(KeywordEntry).where(KeywordEntry.source_type == t, KeywordEntry.source_ref.in_(sorted(refs))))
    _write(conn, recs)


def _group(keys) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t, r in keys:
        out.setdefault(t, set()).add(r)
    return out


def _write(conn, recs: dict) -> None:
    if not recs:
        return
    extras = _extras(conn, recs.keys())
    df, n = _df_context(conn, list(recs.values()))
    rows: list[dict] = []
    for key, rec in recs.items():
        added, off = extras.get(key, ([], []))
        rows += rows_for(rec, df, n, added, off)
    _insert(conn, rows)


# ---- uniqueness: records that look alike get keywords only they have ------------------------------------------------
def _topic(rows: list[dict]) -> set[str]:
    return {r["stem"] for r in rows if r["origin"] in ("title", "file", "manual", "ai") and r["weight"] >= 2.5}


def find_look_alike_groups(recs: dict, rows_by: dict) -> dict:
    """key -> (unique words, look-alike keys) for every record that shares most of its topic keywords with another."""
    topic = {k: _topic(rows_by[k]) for k in recs}
    stems = {k: {r["stem"] for r in rows_by[k] if r["origin"] != "phrase"} | {stem_of(w) for w in terms_of(recs[k].text)} for k in recs}
    inv: dict[str, set] = {}
    for k, t in topic.items():
        for s in t:
            inv.setdefault(s, set()).add(k)
    universal = max(30, int(0.25 * len(recs)))
    out: dict = {}
    for k, t in topic.items():
        counts: Counter = Counter()
        for s in t:
            if len(inv[s]) <= universal:
                counts.update(inv[s])
        neighbors = {o: (topic[o], stems[o]) for o, c in counts.items() if o != k and c >= 2}
        alike = look_alikes(t, neighbors)
        if alike:
            union = set().union(*(stems[o] for o in alike))
            out[k] = (find_unique(Counter(terms_of(recs[k].text)), union), alike)
    return out


# ---- full rebuild ----------------------------------------------------------------------------------------------------
async def _load_all(session) -> dict[tuple[str, str], Rec]:
    recs: dict[tuple[str, str], Rec] = {}
    last = 0
    while True:
        page = (await session.execute(select(Chunk.id, Chunk.document_id, Chunk.question, Chunk.keywords, Chunk.content, Document.title)
                                      .join(Document, Chunk.document_id == Document.id)
                                      .where(Document.status == "ready", Chunk.id > last).order_by(Chunk.id).limit(BATCH))).all()
        if not page:
            break
        for r in page:
            rec = rec_chunk(SimpleNamespace(id=r[0], document_id=r[1], question=r[2], keywords=r[3], content=r[4]), r[5])
            recs[rec.key] = rec
        last = page[-1][0]
    for q in (await session.execute(select(QAPair.id, QAPair.question, QAPair.category, QAPair.answer_template)
                                    .where(QAPair.active.is_(True)))).all():
        rec = rec_qa(SimpleNamespace(id=q[0], question=q[1], category=q[2], answer_template=q[3]))
        recs[rec.key] = rec
    for v in (await session.execute(select(Variable.key, Variable.description))).all():
        rec = rec_variable(SimpleNamespace(key=v[0], description=v[1]))
        recs[rec.key] = rec
    from app.rag.programs import upcoming
    for p in await upcoming(session):
        rec = rec_program(p)
        recs[rec.key] = rec
    for r, sheet in (await session.execute(select(DataRow, DataSheet).join(DataSheet, DataRow.sheet_id == DataSheet.id)
                                           .where(DataSheet.active.is_(True)))).all():
        rec = rec_sheetrow(r, sheet)
        recs[rec.key] = rec
    return recs


async def rebuild_index(session) -> dict:
    """Throw the index away and build it again from the data (nightly job, admin button, first start). Two passes: the
    first counts how many records each word is in, the second picks every record's most distinctive keywords with that
    knowledge; then records that still look alike get unique keywords."""
    recs = await _load_all(session)
    extras_rows = (await session.execute(select(RecordKeywords.source_type, RecordKeywords.source_ref, RecordKeywords.added,
                                                RecordKeywords.removed))).all()
    extras = {(t, r): (list(a or []), list(rm or [])) for t, r, a, rm in extras_rows}
    df: Counter = Counter()
    for rec in recs.values():
        df.update({stem_of(w) for w in terms_of(rec.text)})
    n = len(recs)
    rows_by: dict = {}
    for key, rec in recs.items():
        added, off = extras.get(key, ([], []))
        added = [a for a in added if a.get("origin") != "unique"]       # recomputed below
        rows_by[key] = rows_for(rec, df, n, added, off)

    groups = find_look_alike_groups(recs, rows_by)
    unique_added = 0
    saved: list[dict] = []
    for key in recs:
        added, off = extras.get(key, ([], []))
        keep = [a for a in added if a.get("origin") != "unique"]
        words = groups.get(key, ([], []))[0]
        for w in words:
            for kw in extra_keywords([{"k": w, "origin": "unique"}]):
                if all(r["stem"] != kw.stem for r in rows_by[key]):
                    rows_by[key].append({"stem": kw.stem, "term": kw.term, "source_type": key[0], "source_ref": key[1],
                                         "document_id": recs[key].document_id, "weight": kw.weight, "origin": "unique"})
                    unique_added += 1
        new_added = keep + [{"k": w, "origin": "unique"} for w in words]
        if new_added != added and (new_added or key in extras):
            saved.append({"source_type": key[0], "source_ref": key[1], "added": new_added, "removed": off})

    await session.execute(delete(KeywordEntry))
    counts = {"chunk": 0, "qa": 0, "variable": 0, "program": 0, "sheetrow": 0}
    pending: list[dict] = []
    for key, rows in rows_by.items():
        counts[key[0]] += 1
        pending.extend(rows)
        if len(pending) >= 3000:
            await session.execute(pg_insert(KeywordEntry).on_conflict_do_nothing(constraint="uq_kw_source_stem"), pending)
            pending = []
    if pending:
        await session.execute(pg_insert(KeywordEntry).on_conflict_do_nothing(constraint="uq_kw_source_stem"), pending)
    for row in saved:    # Core upsert: no ORM event, the index rows were just written above
        stmt = pg_insert(RecordKeywords).values(**row)
        await session.execute(stmt.on_conflict_do_update(constraint="uq_record_keywords_source",
                                                         set_={"added": stmt.excluded.added, "updated_at": func.now()}))
    from app.services.settings_service import set_setting
    await set_setting(session, "keyword_index_rebuilt_at", datetime.now(timezone.utc).isoformat())
    await session.commit()
    from app.rag.keyword_search import forget_cache
    forget_cache()
    return {**counts, "unique_keywords_added": unique_added, "look_alike_records": len(groups)}


async def stats(session) -> dict:
    per_type = dict((await session.execute(select(KeywordEntry.source_type, func.count(func.distinct(KeywordEntry.source_ref)))
                                           .group_by(KeywordEntry.source_type))).all())
    entries = (await session.execute(select(func.count()).select_from(KeywordEntry))).scalar_one()
    real = KeywordEntry.origin != "phrase"
    terms = (await session.execute(select(func.count(func.distinct(KeywordEntry.stem))).where(real))).scalar_one()
    top = [{"term": t, "records": n} for t, n in (await session.execute(
        select(func.min(KeywordEntry.term), func.count()).where(real).group_by(KeywordEntry.stem)
        .order_by(func.count().desc()).limit(12))).all()]
    origins = dict((await session.execute(select(KeywordEntry.origin, func.count()).group_by(KeywordEntry.origin))).all())
    from app.services.settings_service import get_setting
    return {"records": per_type, "entries": entries, "keywords": terms, "top_keywords": top, "origins": origins,
            "rebuilt_at": await get_setting(session, "keyword_index_rebuilt_at")}


async def ensure_index(session) -> bool:
    """First start / after a restore: build the index when there is data but no index. True if it was built."""
    if (await session.execute(select(KeywordEntry.id).limit(1))).first():
        return False
    if not (await session.execute(select(Chunk.id).limit(1))).first() and not (await session.execute(select(QAPair.id).limit(1))).first() \
            and not (await session.execute(select(Variable.key).limit(1))).first():
        return False
    await rebuild_index(session)
    return True
