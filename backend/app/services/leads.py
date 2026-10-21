"""The lead list: guests interested in attending a program or in knowing more about the organisation.

The answering AI adds a guest to the list with the create_lead tool when they show that interest
(services/tools.py). Nothing leaves the assistant on its own: staff look at the list (Leads page, also inside the
main app's Social Inbox) and send the leads they choose to the main application, where each becomes a guest + a
task (its Partner API, POST /api/partner/v1/leads) and the lead routing assigns it to a communicator.

Status: new (on the list) -> sent | failed (the main app refused or could not be reached; can be sent again);
dismissed (staff took it off the list; can be restored).
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
STATUSES = ("new", "sent", "failed", "dismissed")


class CannotSend(Exception):
    """Why a lead cannot go to the main application yet (shown to staff)."""


async def record(session: AsyncSession, profile: Profile, conversation: Conversation | None, *, interest: str,
                 note: str = "") -> Lead:
    """Put the guest on the lead list, or refresh their lead for the same interest (a dismissed one comes back)."""
    interest = " ".join((interest or "").split())[:300] or "general interest"
    since = datetime.now(timezone.utc) - SAME_LEAD_WINDOW
    lead = (await session.execute(select(Lead).where(
        Lead.profile_id == profile.id, Lead.interest == interest, Lead.created_at >= since)
        .order_by(Lead.id.desc()).limit(1))).scalar_one_or_none()
    if lead is None:
        program = (await session.execute(select(Program).where(Program.name == interest).limit(1))).scalar_one_or_none()
        lead = Lead(profile_id=profile.id, conversation_id=conversation.id if conversation else None,
                    channel=await _channel(session, conversation), interest=interest,
                    program_id=program.id if program else None, note=(note or "")[:2000] or None, status="new")
        session.add(lead)
        await session.flush()
        return lead
    if note and note not in (lead.note or ""):
        lead.note = ((lead.note + "\n") if lead.note else "") + note[:1000]
    if lead.status == "dismissed":
        lead.status = "new"
    return lead


async def _channel(session: AsyncSession, conversation: Conversation | None) -> str | None:
    if conversation is None:
        return None
    ident = await session.get(ChannelIdentity, conversation.channel_identity_id)
    return ident.channel if ident else None


async def guest_name(session: AsyncSession, profile: Profile) -> str:
    if (profile.name or "").strip():
        return profile.name.strip()
    ident = (await session.execute(select(ChannelIdentity).where(ChannelIdentity.profile_id == profile.id,
                                                                  ChannelIdentity.display_name.is_not(None)).limit(1))).scalar_one_or_none()
    return (ident.display_name or "").strip() if ident else ""


def phone_of(profile: Profile | None) -> str | None:
    return consent_svc.get_phone(profile) if profile and profile.phone_enc else None


async def send(session: AsyncSession, lead: Lead) -> Lead:
    """Staff chose to send this lead: it becomes a guest + a task in the main application.
    Raises CannotSend when it cannot go yet (main app not connected, no phone number, already sent)."""
    if lead.status == "sent":
        raise CannotSend("already sent to the main application")
    if not main_app.configured():
        raise CannotSend("the main application is not connected (MAIN_APP_API_URL / MAIN_APP_API_KEY)")
    profile = await session.get(Profile, lead.profile_id)
    phone = phone_of(profile)
    if not phone:
        raise CannotSend("no phone number: add one on the guest's profile first")
    note = "\n".join(x for x in (
        f"AI assistant lead ({lead.channel or 'chat'}): interested in {lead.interest}",
        lead.note or "",
        f"Assistant conversation #{lead.conversation_id}" if lead.conversation_id else "",
    ) if x)
    payload = {"full_name": ((await guest_name(session, profile)) or "Social guest")[:120], "mobile": phone[:30],
               "email": (consent_svc.get_email(profile) or "")[:200], "location": (profile.location or "")[:200],
               "note": note[:2000]}
    lead.attempts += 1
    try:
        res = await main_app.call("POST", "/api/partner/v1/leads", payload)
    except main_app.MainAppError as exc:
        lead.status, lead.error = "failed", str(exc)[:500]
        logger.warning("lead %s not sent to the main application: %s", lead.id, exc)
        return lead
    lead.status, lead.error, lead.sent_at = "sent", None, datetime.now(timezone.utc)
    lead.main_app_guest_id, lead.main_app_task_id = str(res.get("guest_id") or ""), str(res.get("task_id") or "")
    return lead
