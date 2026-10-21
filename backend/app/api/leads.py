"""The lead list (services/leads.py) and the connection to the main application (services/main_app.py).
The AI fills the list; staff choose which leads to send to the main application as guests + tasks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.api.profiles_admin import mask_phone
from app.core.config import get_settings
from app.db.session import get_session
from app.models import AdminUser, Lead, Profile
from app.services import leads as leads_svc
from app.services import main_app
from app.services.settings_service import get_setting

router = APIRouter(prefix="/api", tags=["leads"])


async def _row(session: AsyncSession, lead: Lead, profile: Profile, admin: bool) -> dict:
    phone = leads_svc.phone_of(profile)
    return {"id": lead.id, "created_at": lead.created_at, "profile_id": lead.profile_id,
            "guest": (await leads_svc.guest_name(session, profile)) or f"Guest #{lead.profile_id}",
            "phone": (phone if admin else mask_phone(phone)) if phone else None,
            "channel": lead.channel, "interest": lead.interest, "note": lead.note, "status": lead.status,
            "error": lead.error, "conversation_id": lead.conversation_id, "main_app_task_id": lead.main_app_task_id,
            "sent_at": lead.sent_at}


@router.get("/leads")
async def list_leads(status: str | None = None, limit: int = 200, offset: int = 0,
                     user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    q = select(Lead, Profile).join(Profile, Profile.id == Lead.profile_id).order_by(Lead.id.desc())
    q = q.where(Lead.status == status) if status in leads_svc.STATUSES else q.where(Lead.status != "dismissed")
    rows = (await session.execute(q.limit(max(1, min(limit, 500))).offset(max(0, offset)))).all()
    counts = dict((await session.execute(select(Lead.status, func.count()).group_by(Lead.status))).all())
    return {
        "items": [await _row(session, lead, profile, user.role == "admin") for lead, profile in rows],
        "counts": {s: counts.get(s, 0) for s in leads_svc.STATUSES},
        "main_app": {"connected": main_app.configured(), "public_url": get_settings().main_app_public_url,
                     "programs_sync": await get_setting(session, "main_app_sync_status")},
    }


class SendIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/leads/send")
async def send_leads(body: SendIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Send the chosen leads to the main application; each answer says sent, failed, or why it could not go."""
    out = []
    for lead_id in dict.fromkeys(body.ids):
        lead = await session.get(Lead, lead_id)
        if lead is None:
            out.append({"id": lead_id, "status": "missing", "error": "not found"})
            continue
        try:
            await leads_svc.send(session, lead)
            out.append({"id": lead.id, "status": lead.status, "error": lead.error, "main_app_task_id": lead.main_app_task_id})
        except leads_svc.CannotSend as exc:
            out.append({"id": lead.id, "status": lead.status, "error": str(exc)})
    await session.commit()
    return {"results": out, "sent": sum(r["status"] == "sent" for r in out)}


class LeadPatch(BaseModel):
    status: str | None = Field(None, pattern="^(new|dismissed)$")   # take off the list / put back
    interest: str | None = Field(None, min_length=1, max_length=300)
    note: str | None = Field(None, max_length=2000)


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: int, body: LeadPatch, user: AdminUser = Depends(any_staff),
                      session: AsyncSession = Depends(get_session)):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    if lead.status == "sent" and body.status:
        raise HTTPException(409, "Already sent to the main application")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(lead, k, v)
    await session.commit()
    return await _row(session, lead, await session.get(Profile, lead.profile_id), user.role == "admin")


@router.post("/main-app/sync-programs", dependencies=[Depends(admin_only)])
async def sync_programs_now(session: AsyncSession = Depends(get_session)):
    if not main_app.configured():
        raise HTTPException(400, "The main application is not connected (MAIN_APP_API_URL / MAIN_APP_API_KEY in .env)")
    return await main_app.sync_programs(session)
