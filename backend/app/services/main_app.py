"""The main application (the organisation's own software, whose Social Inbox shows this assistant).

Two things go through its Partner API (an API key with the scopes events:read and guests:write):
- programs: the events a guest can still join, each with its registration form. They are kept as Program rows
  (source "main_app"), so every answer sees them with their link; the assistant shares the link with a guest who
  wants to attend.
- leads: a guest interested in a program or in knowing more becomes a guest + a task there (services/leads.py).

Nothing here is required: with no MAIN_APP_API_URL / MAIN_APP_API_KEY the assistant works on its own.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Program
from app.rag.documents import bump_context_version
from app.rag.programs import today_bd
from app.services.settings_service import set_setting

logger = logging.getLogger(__name__)
SOURCE = "main_app"
TIMEOUT_S = 10.0


class MainAppError(Exception):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.main_app_api_url.strip() and s.main_app_api_key.strip())


async def call(method: str, path: str, payload: dict | None = None) -> dict:
    s = get_settings()
    if not configured():
        raise MainAppError("the main application is not configured (MAIN_APP_API_URL / MAIN_APP_API_KEY)")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            res = await client.request(method, s.main_app_api_url.rstrip("/") + path, json=payload,
                                       headers={"Authorization": f"Bearer {s.main_app_api_key}"})
    except httpx.HTTPError as exc:
        raise MainAppError(f"main application unreachable: {exc.__class__.__name__}") from exc
    if res.status_code >= 400:
        raise MainAppError(f"main application answered {res.status_code}: {res.text[:300]}")
    return res.json()


def _registration_url(path: str) -> str | None:
    base = get_settings().main_app_public_url.strip().rstrip("/")
    return f"{base}{path}" if base and path else None


def _start_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


async def sync_programs(session: AsyncSession) -> dict:
    """Bring the main application's open programs in (add, update, and remove the ones that closed or are over).
    A failure leaves the programs as they were and is reported in the setting main_app_sync_status."""
    try:
        items = (await call("GET", "/api/partner/v1/programs")).get("programs", [])
    except MainAppError as exc:
        status = {"status": "error", "error": str(exc), "at": datetime.now(timezone.utc).isoformat()}
        await set_setting(session, "main_app_sync_status", status)
        await session.commit()
        return status

    rows = {p.external_id: p for p in (await session.execute(select(Program).where(Program.source == SOURCE))).scalars()}
    seen, changed = set(), 0
    for item in items:
        ext = str(item.get("id") or "")
        if not ext:
            continue
        seen.add(ext)
        start = _start_date(item.get("event_date"))
        notes = [x for x in (
            "registration is full" if item.get("seats_full") else "",
            f"helpline: {item['helpline']}" if item.get("helpline") else "",
            item.get("note") or "",
        ) if x]
        values = {
            "name": (item.get("title") or item.get("name") or "")[:160],
            # no date yet: kept as "today" so it stays on the schedule, its date shown as not announced
            "start_date": start or today_bd(),
            "end_date": None,
            "date_text": item.get("display_date") or ("" if start else "date not announced yet"),
            "fee": f"{item['fee']} টাকা" if item.get("fee") else None,
            "place": (item.get("place") or "")[:200] or None,
            "notes": " · ".join(notes)[:1000] or None,
            "registration_url": _registration_url(item.get("registration_path") or ""),
        }
        row = rows.get(ext)
        if row is None:
            session.add(Program(source=SOURCE, external_id=ext, **values))
            changed += 1
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            changed += 1
    for ext, row in rows.items():
        if ext not in seen:  # closed, unpublished or over in the main app
            await session.delete(row)
            changed += 1
    if changed:
        await bump_context_version(session)
    status = {"status": "ok", "programs": len(seen), "changed": changed, "at": datetime.now(timezone.utc).isoformat()}
    await set_setting(session, "main_app_sync_status", status)
    await session.commit()
    return status
