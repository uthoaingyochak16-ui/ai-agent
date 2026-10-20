"""The lead list (services/leads.py) and the connection to the main application (services/main_app.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.core.config import get_settings
from app.db.session import get_session
from app.models import ChannelIdentity, Lead, Profile
from app.services import leads as leads_svc
from app.services import main_app
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api", tags=["leads"], dependencies=[Depends(any_staff)])
STATUSES = ("pending", "sent", "failed", "off")


@router.get("/leads")
async def list_leads(status: str | None = None, limit: int = 100, offset: int = 0,
                     session: AsyncSession = Depends(get_session)):
    q = select(Lead, Profile.name).join(Profile, Profile.id == Lead.profile_id).order_by(Lead.id.desc())
    if status in STATUSES:
        q = q.where(Lead.status == status)
    rows = (await session.execute(q.limit(max(1, min(limit, 500))).offset(max(0, offset)))).all()
    names = {pid: n for pid, n in (await session.execute(
        select(ChannelIdentity.profile_id, ChannelIdentity.display_name).where(
            ChannelIdentity.profile_id.in_([lead.profile_id for lead, _ in rows])))).all() if n}
    counts = dict((await session.execute(select(Lead.status, func.count()).group_by(Lead.status))).all())
    return {
        "items": [{"id": lead.id, "created_at": lead.created_at, "profile_id": lead.profile_id,
                   "guest": name or names.get(lead.profile_id) or f"Guest #{lead.profile_id}",
                   "channel": lead.channel, "interest": lead.interest, "note": lead.note, "status": lead.status,
                   "error": lead.error, "conversation_id": lead.conversation_id,
                   "main_app_task_id": lead.main_app_task_id, "sent_at": lead.sent_at} for lead, name in rows],
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "main_app": {"connected": main_app.configured(), "public_url": get_settings().main_app_public_url,
                     "programs_sync": await get_setting(session, "main_app_sync_status")},
    }


@router.post("/leads/{lead_id}/send")
async def send_lead(lead_id: int, session: AsyncSession = Depends(get_session)):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    if lead.status != "sent":
        await leads_svc.send(session, lead)
        await session.commit()
    return {"id": lead.id, "status": lead.status, "error": lead.error, "main_app_task_id": lead.main_app_task_id}


@router.post("/main-app/sync-programs", dependencies=[Depends(admin_only)])
async def sync_programs_now(session: AsyncSession = Depends(get_session)):
    if not main_app.configured():
        raise HTTPException(400, "The main application is not connected (MAIN_APP_API_URL / MAIN_APP_API_KEY in .env)")
    return await main_app.sync_programs(session)
