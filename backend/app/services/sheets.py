"""Data sheets: tables of facts kept like a spreadsheet (Books: name, edition, author, publication, price, description).

How the AI uses them:
- every row is a record of the keyword index (source_type "sheetrow"), so a guest question that names a book, an author
  or a branch brings the matching rows into the answer's CONTEXT as "SHEET <name> — Column: value | ..." lines, the same
  way Q&A and program records come in (rag/keyword_search.py);
- the search_sheet tool (services/tools.py) lets the model look a sheet up itself, or list it ("which books did X
  write?", "all books under 300 taka").
A sheet that is switched off (active=False) is used by neither.
"""
from __future__ import annotations

import re

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataRow, DataSheet

MAX_COLUMNS = 30
MAX_CELL = 4000
_WORD = re.compile(r"[\wঀ-৿]+")


def slug(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9ঀ-৿]+", "_", label.strip().lower()).strip("_")[:40] or "column"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key


def clean_columns(cols: list, old: list | None = None) -> list[dict]:
    """[{"key", "label"}]: a column keeps its key when its label changes (its values stay), new ones get a key."""
    known = {c["key"]: c for c in old or []}
    taken: set[str] = set()
    out: list[dict] = []
    for c in cols[:MAX_COLUMNS]:
        label = (c.get("label") if isinstance(c, dict) else str(c or "")).strip()[:80]
        if not label:
            continue
        key = c.get("key") if isinstance(c, dict) and c.get("key") in known and c["key"] not in taken else None
        if key:
            taken.add(key)
        out.append({"key": key or slug(label, taken | set(known)), "label": label})
    return out


def clean_values(sheet: DataSheet, values: dict) -> dict:
    keys = {c["key"] for c in sheet.columns or []}
    return {k: " ".join(str(v).split())[:MAX_CELL] for k, v in (values or {}).items() if k in keys and v not in (None, "")}


def search_text(sheet: DataSheet, values: dict) -> str:
    return " ".join(str(values.get(c["key"], "")) for c in sheet.columns or []).lower()


def row_line(sheet: DataSheet, values: dict) -> str:
    """One row as the AI reads it."""
    cells = [f"{c['label']}: {values[c['key']]}" for c in sheet.columns or [] if values.get(c["key"])]
    return f"SHEET {sheet.name} — " + " | ".join(cells)


def index_parts(sheet: DataSheet, values: dict) -> dict:
    """The keyword-index record of a row: the first column is its name, the sheet its topic, the rest its text."""
    cols = sheet.columns or []
    first = values.get(cols[0]["key"], "") if cols else ""
    rest = " ".join(f"{c['label']} {values[c['key']]}" for c in cols[1:] if values.get(c["key"]))
    return {"title": first or None, "meta": sheet.name, "doc": sheet.description, "body": rest or None}


async def find_sheet(session: AsyncSession, name: str | None) -> DataSheet | None:
    if not name:
        return None
    n = name.strip().lower()
    sheets = (await session.execute(select(DataSheet).where(DataSheet.active.is_(True)))).scalars().all()
    return next((s for s in sheets if s.name.lower() == n), None) or next((s for s in sheets if n in s.name.lower()
                                                                           or s.name.lower() in n), None)


async def search(session: AsyncSession, sheet: DataSheet | None, query: str, limit: int = 10) -> list[tuple[DataSheet, DataRow, int]]:
    """Rows that contain the query's words, most words first; no words = the sheet's first rows (a list request)."""
    words = [w for w in (w.lower() for w in _WORD.findall(query or "")) if len(w) >= 2][:12]
    q = select(DataRow, DataSheet).join(DataSheet, DataRow.sheet_id == DataSheet.id).where(DataSheet.active.is_(True))
    if sheet is not None:
        q = q.where(DataRow.sheet_id == sheet.id)
    if not words:
        if sheet is None:
            return []
        rows = (await session.execute(q.order_by(DataRow.position, DataRow.id).limit(limit))).all()
        return [(s, r, 0) for r, s in rows]
    score = sum((case((DataRow.search_text.contains(w), 1), else_=0) for w in words), start=0)
    rows = (await session.execute(q.add_columns(score.label("score")).where(score > 0)
                                  .order_by(score.desc(), DataRow.position, DataRow.id).limit(limit))).all()
    return [(s, r, int(sc)) for r, s, sc in rows]


async def overview(session: AsyncSession) -> list[dict]:
    counts = dict((await session.execute(select(DataRow.sheet_id, func.count()).group_by(DataRow.sheet_id))).all())
    return [{"id": s.id, "name": s.name, "description": s.description, "columns": s.columns or [], "active": s.active,
             "rows": counts.get(s.id, 0), "updated_at": s.updated_at}
            for s in (await session.execute(select(DataSheet).order_by(DataSheet.name))).scalars()]


async def prompt_block(session: AsyncSession) -> str:
    """What the AI is told about the sheets it may search (names, what they are for, their columns)."""
    sheets = [s for s in await overview(session) if s["active"] and s["rows"]]
    if not sheets:
        return ""
    lines = [f"- {s['name']} ({s['rows']} rows; columns: {', '.join(c['label'] for c in s['columns'])})"
             + (f": {s['description'][:200]}" if s["description"] else "") for s in sheets]
    return ("\nDATA SHEETS: tables of facts. Lines starting with \"SHEET\" in CONTEXT are rows of them. When the guest asks "
            "about something a sheet holds and CONTEXT lacks it (or they want a list), call search_sheet. Sheets:\n"
            + "\n".join(lines))
