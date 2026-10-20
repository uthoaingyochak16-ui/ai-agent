"""The lead list: guests interested in attending a program or in knowing more about the organisation.

The answering AI records a lead with the create_lead tool when a guest shows that interest (services/tools.py).
A lead is sent to the main application as a guest + a task (its Partner API, POST /api/partner/v1/leads), where the
lead routing assigns it to a communicator. That needs a phone number: until the guest has given one, or while the main
application cannot be reached, the lead waits as "pending" and the worker tries again (send_pending_job).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelIdentity, Conversation, Lead, Profile, Program
from app.services import consent as consent_svc
from app.services import main_app

logger = logging.getLogger(__name__)
SAME_LEAD_WINDOW = timedelta(days=30)   # the same interest again within this time updates the lead instead
MAX_ATTEMPTS = 20
RETRY_WINDOW = timedelta(days=14)


async def record(session: AsyncSession, profile: Profile, conversation: Conversation | None, *, interest: str,
                 note: str = "") -> Lead:
    """Add the guest to the lead list (or refresh their open lead for the same interest) and try to send it."""
    interest = " ".join((interest or "").split())[:300] or "general interest"
    since = datetime.now(timezone.utc) - SAME_LEAD_WINDOW
    lead = (await session.execute(select(Lead).where(
        Lead.profile_id == profile.id, Lead.interest == interest, Lead.created_at >= since)
        .order_by(Lead.id.desc()).limit(1))).scalar_one_or_none()
    if lead is None:
        program = (await session.execute(select(Program).where(Program.name == interest).limit(1))).scalar_one_or_none()
        lead = Lead(profile_id=profile.id, conversation_id=conversation.id if conversation else None,
                    channel=await _channel(session, conversation), interest=interest,
                    program_id=program.id if program else None, note=(note or "")[:2000] or None, status="pending")
        session.add(lead)
        await session.flush()
    elif note and note not in (lead.note or ""):
        lead.note = ((lead.note + "\n") if lead.note else "") + note[:1000]
    if lead.status != "sent":
        await send(session, lead, profile)
    return lead


async def _channel(session: AsyncSession, conversation: Conversation | None) -> str | None:
    if conversation is None:
        return None
    ident = await session.get(ChannelIdentity, conversation.channel_identity_id)
    return ident.channel if ident else None


async def send(session: AsyncSession, lead: Lead, profile: Profile | None = None) -> None:
    """Send one lead to the main application if it can go now; otherwise note why it waits."""
    if not main_app.configured():
        lead.status, lead.error = "off", "the main application is not connected"
        return
    profile = profile or await session.get(Profile, lead.profile_id)
    phone = consent_svc.get_phone(profile) if profile and profile.phone_enc else None
    if not phone:
        lead.status, lead.error = "pending", "waiting for the guest's phone number"
        return
    name = (profile.name or "").strip() or await _display_name(session, profile) or "Social guest"
    note = "\n".join(x for x in (
        f"AI assistant lead ({lead.channel or 'chat'}): interested in {lead.interest}",
        lead.note or "",
        f"Assistant conversation #{lead.conversation_id}" if lead.conversation_id else "",
    ) if x)
    payload = {"full_name": name[:120], "mobile": phone[:30], "email": (consent_svc.get_email(profile) or "")[:200],
               "location": (profile.location or "")[:200], "note": note[:2000]}
    lead.attempts += 1
    try:
        res = await main_app.call("POST", "/api/partner/v1/leads", payload)
    except main_app.MainAppError as exc:
        lead.error = str(exc)[:500]
        lead.status = "failed" if lead.attempts >= MAX_ATTEMPTS else "pending"
        logger.warning("lead %s not sent to the main application: %s", lead.id, exc)
        return
    lead.status, lead.error, lead.sent_at = "sent", None, datetime.now(timezone.utc)
    lead.main_app_guest_id, lead.main_app_task_id = str(res.get("guest_id") or ""), str(res.get("task_id") or "")


async def _display_name(session: AsyncSession, profile: Profile) -> str:
    ident = (await session.execute(select(ChannelIdentity).where(ChannelIdentity.profile_id == profile.id,
                                                                  ChannelIdentity.display_name.is_not(None)).limit(1))).scalar_one_or_none()
    return (ident.display_name or "").strip() if ident else ""


async def send_pending(session: AsyncSession) -> int:
    """Worker job: try the waiting leads again (a phone may have arrived, the main app may be back). Returns how many
    were sent."""
    since = datetime.now(timezone.utc) - RETRY_WINDOW
    waiting = (await session.execute(select(Lead).where(Lead.status.in_(("pending", "off")), Lead.created_at >= since)
                                     .order_by(Lead.id).limit(100))).scalars().all()
    sent = 0
    for lead in waiting:
        await send(session, lead)
        sent += lead.status == "sent"
    await session.commit()
    return sent
