"""Programs table (Q&A manager -> Programs): upcoming courses/events the assistant answers date / fee / place questions from."""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.db.session import get_session
from app.models import Program
from app.rag.documents import bump_context_version
from app.rag.programs import purge_expired, today_bd, upcoming
from app.services.program_sync import DEFAULT_PROGRAM_SYNC_URL, sync_programs
from app.services.settings_service import get_setting, set_setting

router = APIRouter(prefix="/api/programs", tags=["programs"], dependencies=[Depends(any_staff)])
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
           "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
           "september": 9, "oct": 10, "october": 10, "octobar": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
_TEXT_DATE = re.compile(r"(?i)\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\.?\s*(\d{4})?\b")


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    date_text: str | None = Field(None, max_length=300)
    start_date: date | None = None  # compatibility with older API clients
    end_date: date | None = None
    fee: str | None = Field(None, max_length=2000)
    place: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=1000)


def _text_dates(value: str) -> list[date]:
    today = today_bd()
    out: list[date] = []
    for day, month, year in _TEXT_DATE.findall(value):
        y = int(year) if year else today.year
        try:
            parsed = date(y, _MONTHS[month.lower()], int(day))
            if not year and parsed < today:  # e.g. "12 January" means the next occurrence
                parsed = date(y + 1, _MONTHS[month.lower()], int(day))
        except ValueError:
            raise HTTPException(422, "Enter a valid calendar date") from None
        out.append(parsed)
    return out


def _dates(body: ProgramIn) -> tuple[date, date | None]:
    if body.date_text and body.date_text.strip():
        found = [date.fromisoformat(item) for item in _DATE.findall(body.date_text)]
        if not found:
            found = _text_dates(body.date_text)
        if not found:
            raise HTTPException(422, "Enter a date such as 12 October or 2026-10-12")
        return found[0], found[1] if len(found) > 1 else None
    if body.start_date:
        return body.start_date, body.end_date
    raise HTTPException(422, "Enter the program date")


def _date_text(p: Program) -> str:
    return p.date_text or (p.start_date.isoformat() + (f" to {p.end_date.isoformat()}" if p.end_date and p.end_date != p.start_date else ""))


def _row(p: Program, today: date) -> dict:
    return {"id": p.id, "name": p.name, "date_text": _date_text(p), "start_date": p.start_date, "end_date": p.end_date, "fee": p.fee, "place": p.place,
            "notes": p.notes, "days_left": max(0, (p.start_date - today).days), "running": p.start_date <= today}


def _check(body: ProgramIn, today: date) -> tuple[date, date | None]:
    start, end = _dates(body)
    if end and end < start:
        raise HTTPException(422, "The last day cannot be before the first day")
    if (end or start) < today:
        raise HTTPException(422, "This date has already passed. Programs are removed the day after they end")
    return start, end


def _apply(p: Program, body: ProgramIn, start: date, end: date | None) -> None:
    p.name, p.date_text, p.start_date, p.end_date = body.name.strip(), (body.date_text or "").strip() or None, start, end
    p.fee, p.place, p.notes = (body.fee or "").strip() or None, (body.place or "").strip() or None, (body.notes or "").strip() or None


@router.get("")
async def list_programs(session: AsyncSession = Depends(get_session)):
    today = today_bd()
    if await purge_expired(session, today):
        await bump_context_version(session)
    await session.commit()
    return {"today": today, "items": [_row(p, today) for p in await upcoming(session, today)]}


@router.get("/sync/status")
async def program_sync_status(session: AsyncSession = Depends(get_session)):
    return {"enabled": bool(await get_setting(session, "program_sync_enabled")),
            "source_url": await get_setting(session, "program_sync_url") or DEFAULT_PROGRAM_SYNC_URL,
            "last": await get_setting(session, "program_sync_status")}


@router.post("/sync", dependencies=[Depends(admin_only)])
async def program_sync(session: AsyncSession = Depends(get_session)):
    result = await sync_programs(session)
    await session.commit()
    return result


@router.post("")
async def create_program(body: ProgramIn, session: AsyncSession = Depends(get_session)):
    today = today_bd()
    start, end = _check(body, today)
    p = Program()
    _apply(p, body, start, end)
    session.add(p)
    await bump_context_version(session)
    await session.commit()
    return _row(p, today)


@router.put("/{pid}")
async def update_program(pid: int, body: ProgramIn, session: AsyncSession = Depends(get_session)):
    p = await session.get(Program, pid)
    if not p:
        raise HTTPException(404, "Not found")
    today = today_bd()
    start, end = _check(body, today)
    _apply(p, body, start, end)
    await bump_context_version(session)
    await session.commit()
    return _row(p, today)


@router.delete("/{pid}")
async def delete_program(pid: int, session: AsyncSession = Depends(get_session)):
    p = await session.get(Program, pid)
    if p:
        await session.delete(p)
        await bump_context_version(session)
        await session.commit()
    return {"ok": True}
