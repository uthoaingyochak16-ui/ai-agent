"""Safe, one-way import of public program schedules from Quantum Method.

Only public event metadata is imported.  A failed or changed website never deletes
an existing program: it is reported in the sync status for an administrator to see.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Program
from app.rag.documents import bump_context_version
from app.rag.programs import today_bd
from app.services.settings_service import get_setting, set_setting

DEFAULT_PROGRAM_SYNC_URL = "https://event.quantummethod.org.bd/api/event?language=bn"
SOURCE_MARKER = "website_sync:quantummethod.org.bd/bn"
_SCRIPTS = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _title_tokens(value: str) -> set[str]:
    """Loose title identity for website titles versus staff-entered display titles."""
    text = unicodedata.normalize("NFC", value or "").lower().replace("য়", "য়")
    return {part for part in re.findall(r"[\w\u0980-\u09ff]+", text) if len(part) > 1}


def _same_title(left: str, right: str) -> bool:
    a, b = _title_tokens(left), _title_tokens(right)
    if a == b:
        return True
    shared = len(a & b)
    # Compare against the larger title, so distinct variants such as "Yoga Orientation—Male"
    # and "Yoga Orientation—Female" are never merged just because their common words match.
    return shared >= 2 and shared / max(len(a), len(b)) >= 0.8


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def _events(value: object) -> list[dict]:
    """Return Event objects from a JSON-LD tree, including @graph/list wrappers."""
    if isinstance(value, list):
        return [event for item in value for event in _events(item)]
    if not isinstance(value, dict):
        return []
    found: list[dict] = []
    kind = value.get("@type")
    if kind == "Event" or (isinstance(kind, list) and "Event" in kind):
        found.append(value)
    for key in ("@graph", "itemListElement"):
        found.extend(_events(value.get(key)))
    return found


def parse_programs_page(page: str, today: date | None = None) -> list[dict]:
    """Extract future published Event records from the official API or Event JSON-LD."""
    today = today or today_bd()
    records: list[dict] = []
    # The Angular public site publishes its schedule through this official JSON API.
    # Supporting it first avoids brittle scraping of rendered page text.
    try:
        payload = json.loads(page)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        for event in payload["results"]:
            if not isinstance(event, dict) or event.get("is_published") is False:
                continue
            start = _as_date(event.get("start_date"))
            end = _as_date(event.get("end_date")) or start
            name = _SPACE.sub(" ", str(event.get("title") or "")).strip()
            place = _SPACE.sub(" ", str(event.get("venue") or "")).strip() or None
            if name and start and start >= today:
                records.append({"name": name, "start_date": start, "end_date": end,
                                "date_text": start.isoformat() + (f" to {end.isoformat()}" if end and end != start else ""), "place": place})
    for raw in _SCRIPTS.findall(page):
        try:
            tree = json.loads(html.unescape(raw).strip())
        except json.JSONDecodeError:
            continue
        for event in _events(tree):
            start = _as_date(event.get("startDate"))
            end = _as_date(event.get("endDate")) or start
            name = _SPACE.sub(" ", str(event.get("name") or "")).strip()
            location = event.get("location")
            if isinstance(location, dict):
                location = location.get("name") or location.get("address")
            place = _SPACE.sub(" ", str(location or "")).strip() or None
            if name and start and start >= today:
                records.append({"name": name, "start_date": start, "end_date": end,
                                "date_text": str(event.get("startDate")), "place": place})
    # Do not scrape arbitrary page text: that could turn old news into a live program.
    unique: dict[tuple[str, date, date | None], dict] = {}
    for record in records:
        unique[(record["name"], record["start_date"], record["end_date"])] = record
    return list(unique.values())


async def sync_programs(session: AsyncSession, url: str | None = None) -> dict:
    """Fetch the configured public schedule and add/update safely matched records."""
    url = (url or await get_setting(session, "program_sync_url") or DEFAULT_PROGRAM_SYNC_URL).strip()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "QuantumAssistant/1.0 schedule sync"}) as client:
            response = await client.get(url)
            response.raise_for_status()
        candidates = parse_programs_page(response.text)
        if not candidates:
            result = {"status": "warning", "last_run": now, "source_url": url, "found": 0,
                      "added": 0, "updated": 0, "message": "No publishable Event schedule was found; existing programs were kept."}
            await set_setting(session, "program_sync_status", result)
            return result
        added = updated = merged = 0
        for item in candidates:
            same_day = list((await session.execute(select(Program).where(Program.start_date == item["start_date"]))).scalars())
            existing = next((p for p in same_day if _same_title(p.name, item["name"])), None)
            if existing is None:
                session.add(Program(**item, notes=f"{SOURCE_MARKER}; automatically imported"))
                added += 1
            else:
                # Public source controls name/date/venue; a staff-entered fee is retained.
                existing.name, existing.date_text = item["name"], item["date_text"]
                existing.start_date, existing.end_date, existing.place = item["start_date"], item["end_date"], item["place"]
                existing.notes = f"{SOURCE_MARKER}; automatically imported"
                updated += 1
        # Remove duplicate versions of the same source event while retaining any staff-entered fee.
        all_rows = list((await session.execute(select(Program))).scalars())
        remaining = list(all_rows)
        for source in list(all_rows):
            if source not in remaining or SOURCE_MARKER not in (source.notes or ""):
                continue
            for other in list(remaining):
                if other.id == source.id or other.start_date != source.start_date or not _same_title(other.name, source.name):
                    continue
                if not source.fee and other.fee:
                    source.fee = other.fee
                await session.delete(other)
                remaining.remove(other)
                merged += 1
        if added or updated:
            await bump_context_version(session)
        result = {"status": "done", "last_run": now, "source_url": url, "found": len(candidates), "added": added, "updated": updated, "merged": merged}
        await set_setting(session, "program_sync_status", result)
        return result
    except (httpx.HTTPError, ValueError) as exc:
        result = {"status": "error", "last_run": now, "source_url": url, "found": 0, "added": 0, "updated": 0, "message": str(exc)[:300]}
        await set_setting(session, "program_sync_status", result)
        return result
