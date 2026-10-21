"""Data sheets (services/sheets.py): spreadsheet-like tables the AI searches. Staff edit rows; creating, changing the
columns and deleting a sheet is for admins. Import a CSV / Excel / JSON file as a new sheet or into one; export as CSV."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.api.qa import _read_records, _safe_cell
from app.db.session import get_session
from app.models import AdminUser, DataRow, DataSheet, KeywordEntry
from app.rag.documents import bump_context_version
from app.services import sheets as S

router = APIRouter(prefix="/api/sheets", tags=["data sheets"])
MAX_ROWS_IMPORT = 5000


class Column(BaseModel):
    key: str | None = Field(None, max_length=60)
    label: str = Field(min_length=1, max_length=80)


class SheetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)
    columns: list[Column] = Field(min_length=1, max_length=S.MAX_COLUMNS)
    active: bool = True


class SheetPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)
    columns: list[Column] | None = Field(None, min_length=1, max_length=S.MAX_COLUMNS)
    active: bool | None = None


class RowIn(BaseModel):
    values: dict[str, str | int | float | None]


async def _sheet(session: AsyncSession, sheet_id: int) -> DataSheet:
    s = await session.get(DataSheet, sheet_id)
    if s is None:
        raise HTTPException(404, "Sheet not found")
    return s


async def _unique_name(session: AsyncSession, name: str, own: int | None = None) -> str:
    name = " ".join(name.split())
    other = (await session.execute(select(DataSheet.id).where(func.lower(DataSheet.name) == name.lower()))).scalar_one_or_none()
    if other is not None and other != own:
        raise HTTPException(409, f"A sheet called '{name}' already exists")
    return name


def _row(r: DataRow) -> dict:
    return {"id": r.id, "values": r.values or {}, "updated_at": r.updated_at}


async def _changed(session: AsyncSession) -> None:
    await bump_context_version(session)   # cached answers were made without this change


@router.get("")
async def list_sheets(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return await S.overview(session)


@router.post("")
async def create_sheet(body: SheetIn, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    s = DataSheet(name=await _unique_name(session, body.name), description=(body.description or "").strip() or None,
                  columns=S.clean_columns([c.model_dump() for c in body.columns]), active=body.active)
    if not s.columns:
        raise HTTPException(422, "Give the sheet at least one column")
    session.add(s)
    await session.commit()
    return next(x for x in await S.overview(session) if x["id"] == s.id)


@router.put("/{sheet_id}")
async def update_sheet(sheet_id: int, body: SheetPatch, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    if body.name is not None:
        s.name = await _unique_name(session, body.name, own=s.id)
    if body.description is not None:
        s.description = body.description.strip() or None
    if body.active is not None:
        s.active = body.active
    if body.columns is not None:
        cols = S.clean_columns([c.model_dump() for c in body.columns], s.columns)
        if not cols:
            raise HTTPException(422, "Give the sheet at least one column")
        s.columns = cols
        keys = {c["key"] for c in cols}
        for r in (await session.execute(select(DataRow).where(DataRow.sheet_id == s.id))).scalars():
            r.values = {k: v for k, v in (r.values or {}).items() if k in keys}   # a removed column's cells go
            r.search_text = S.search_text(s, r.values)
    await _changed(session)
    await session.commit()
    return next(x for x in await S.overview(session) if x["id"] == s.id)


@router.delete("/{sheet_id}")
async def delete_sheet(sheet_id: int, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    ids = [str(i) for i in (await session.execute(select(DataRow.id).where(DataRow.sheet_id == s.id))).scalars()]
    if ids:   # the rows go with the sheet (database cascade): take them out of the keyword index first
        await session.execute(delete(KeywordEntry).where(KeywordEntry.source_type == "sheetrow", KeywordEntry.source_ref.in_(ids)))
    await session.delete(s)
    await _changed(session)
    await session.commit()
    return {"deleted": sheet_id}


@router.get("/{sheet_id}/rows")
async def list_rows(sheet_id: int, q: str | None = None, limit: int = 200, offset: int = 0,
                    _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    query = select(DataRow).where(DataRow.sheet_id == s.id)
    if q and q.strip():
        for w in q.lower().split()[:8]:
            query = query.where(DataRow.search_text.contains(w))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(DataRow.position, DataRow.id).limit(max(1, min(limit, 1000))).offset(max(0, offset)))).scalars()
    return {"items": [_row(r) for r in rows], "total": total}


@router.post("/{sheet_id}/rows")
async def add_row(sheet_id: int, body: RowIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    values = S.clean_values(s, body.values)
    last = (await session.execute(select(func.max(DataRow.position)).where(DataRow.sheet_id == s.id))).scalar_one() or 0
    r = DataRow(sheet_id=s.id, values=values, search_text=S.search_text(s, values), position=last + 1)
    session.add(r)
    await _changed(session)
    await session.commit()
    return _row(r)


@router.put("/{sheet_id}/rows/{row_id}")
async def update_row(sheet_id: int, row_id: int, body: RowIn, _: AdminUser = Depends(any_staff),
                     session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    r = await session.get(DataRow, row_id)
    if r is None or r.sheet_id != s.id:
        raise HTTPException(404, "Row not found")
    r.values = S.clean_values(s, body.values)
    r.search_text = S.search_text(s, r.values)
    await _changed(session)
    await session.commit()
    return _row(r)


@router.delete("/{sheet_id}/rows/{row_id}")
async def delete_row(sheet_id: int, row_id: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    r = await session.get(DataRow, row_id)
    if r is None or r.sheet_id != sheet_id:
        raise HTTPException(404, "Row not found")
    await session.delete(r)
    await _changed(session)
    await session.commit()
    return {"deleted": row_id}


@router.post("/import")
async def import_sheet(file: UploadFile = File(...), sheet_id: int | None = Form(None), name: str | None = Form(None),
                       description: str | None = Form(None), _: AdminUser = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)):
    """The file's first row is the column names. With sheet_id the rows are added to that sheet (columns matched by
    name; new names become new columns); without it a new sheet is made, named after `name` or the file."""
    data = await file.read(10 * 1024 * 1024)
    records = _read_records(data, (file.filename or "").rsplit(".", 1)[-1].lower())[:MAX_ROWS_IMPORT]
    heads = [h for h in dict.fromkeys(str(k).strip() for r in records for k in r) if h]
    if not heads:
        raise HTTPException(422, "The file has no columns: its first row must hold the column names")
    if sheet_id is not None:
        s = await _sheet(session, sheet_id)
        have = {c["label"].lower() for c in s.columns or []}
        s.columns = S.clean_columns([*(s.columns or []), *({"label": h} for h in heads if h.lower() not in have)], s.columns)
    else:
        base = (name or (file.filename or "Sheet").rsplit(".", 1)[0]).strip()[:120] or "Sheet"
        s = DataSheet(name=await _unique_name(session, base), description=(description or "").strip() or None,
                      columns=S.clean_columns([{"label": h} for h in heads]))
        session.add(s)
        await session.flush()
    by_label = {c["label"].lower(): c["key"] for c in s.columns}
    last = (await session.execute(select(func.max(DataRow.position)).where(DataRow.sheet_id == s.id))).scalar_one() or 0
    added = 0
    for rec in records:
        values = S.clean_values(s, {by_label.get(str(k).strip().lower()): v for k, v in rec.items()})
        if not values:
            continue
        added += 1
        session.add(DataRow(sheet_id=s.id, values=values, search_text=S.search_text(s, values), position=last + added))
    await _changed(session)
    await session.commit()
    return {"sheet": next(x for x in await S.overview(session) if x["id"] == s.id), "added": added,
            "skipped": len(records) - added}


@router.get("/{sheet_id}/export")
async def export_sheet(sheet_id: int, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    s = await _sheet(session, sheet_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in s.columns])
    for r in (await session.execute(select(DataRow).where(DataRow.sheet_id == s.id).order_by(DataRow.position, DataRow.id))).scalars():
        w.writerow([_safe_cell(str((r.values or {}).get(c["key"], ""))) for c in s.columns])
    fname = "".join(ch if ch.isalnum() else "_" for ch in s.name)[:60] or "sheet"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{fname}.csv"'})
