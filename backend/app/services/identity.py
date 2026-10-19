"""Channel identity -> profile resolution, and cross-channel linking so one guest = one profile.

How the same person is recognised across Pages, Instagram and WhatsApp:
  - Meta says so (ids_for_pages: the guest's id on another Page of the same Business)      -> merged automatically
  - a WhatsApp number (verified by WhatsApp) equals a phone the guest gave on another channel -> merged automatically
    when the names agree, otherwise suggested
  - a phone/email typed in chat that another profile already has                              -> same rule
  - the same full name on another Page                                                         -> suggested (admin merges
    with one click), because two different people can share a name
Typed phones/emails are remembered as a keyed hash (fact "merge_idx_<kind>", unreadable) even before consent, so a
later WhatsApp message from that number still finds the guest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import IncomingMessage
from app.core import crypto
from app.models import ChannelIdentity, Conversation, Profile, ProfileFact
from app.rag.search import norm
from app.services import consent as consent_svc
from app.services import contact_owner
from app.services.contacts import extract_emails, extract_phones, normalize_phone
from app.services.merge import MergeError, merge_profiles
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)


@dataclass
class Resolved:
    identity: ChannelIdentity
    profile: Profile
    conversation: Conversation
    is_new_identity: bool = False
    events: list[str] = field(default_factory=list)  # human-readable trace (debug panel)
    debug: dict | None = None   # pipeline debug of the reply (Test Chat)
    post: dict | None = None    # post-reply extraction/summary results
    prev_user_message_at: datetime | None = None  # this identity's previous message, before the current one
    reply_prefix: str = ""  # lead capture: put around the next reply ("thanks" / "we also need ..."), used once
    reply_suffix: str = ""
    notice: str = ""        # e.g. the card warning: put first in the next reply, used once
    turn_text: str = ""     # what the guest wrote this turn (a whole burst), for guest-info collection
    followups: list[str] = field(default_factory=list)  # extra short bubbles sent after the next reply, used once
    qtype: str | None = None  # question type of this turn (services/reply_style.py)


async def _active(session: AsyncSession, profile_id: int) -> Profile:
    p = await session.get(Profile, profile_id)
    seen = set()
    while p is not None and p.merged_into_id and p.id not in seen:
        seen.add(p.id)
        p = await session.get(Profile, p.merged_into_id)
    assert p is not None
    return p


async def resolve(session: AsyncSession, msg: IncomingMessage) -> Resolved:
    res = await session.execute(pg_insert(ChannelIdentity).values(
        channel=msg.channel, external_id=msg.external_user_id, display_name=msg.sender_name)
        .on_conflict_do_nothing(constraint="uq_channel_external").returning(ChannelIdentity.id))
    is_new = res.first() is not None
    ident = (await session.execute(select(ChannelIdentity).where(
        ChannelIdentity.channel == msg.channel, ChannelIdentity.external_id == msg.external_user_id)
        .with_for_update())).scalar_one()
    if msg.recipient_id and ident.account_id != str(msg.recipient_id):  # the Page / number they wrote to this time
        ident.account_id = str(msg.recipient_id)
    events: list[str] = []

    pending_suggestion: int | None = None
    if ident.profile_id is None:
        profile: Profile | None = None
        if msg.channel == "whatsapp":  # WhatsApp hands us the verified phone number
            e164 = normalize_phone("+" + msg.external_user_id)
            if e164:
                idx = crypto.blind_index(e164, "phone")
                profile = (await session.execute(select(Profile).where(
                    Profile.phone_hash == idx, Profile.merged_into_id.is_(None)))).scalar_one_or_none()
                if profile:
                    events.append("whatsapp number matched an existing profile")
                else:  # the number was typed on Messenger/Instagram (held until consent): same guest if names agree
                    claimed = await _profile_with_index(session, "phone", idx, exclude_id=0)
                    if claimed is not None:
                        if not msg.sender_name or _same_name({norm(msg.sender_name)}, await _names(session, claimed.id)):
                            profile = claimed
                            events.append("whatsapp number matched a phone given on another channel")
                        else:
                            pending_suggestion = claimed.id
        if profile is None:
            profile = Profile()
            session.add(profile)
            await session.flush()
            events.append("created anonymous profile")
        ident.profile_id = profile.id
        if pending_suggestion:
            await suggest_merge(session, profile.id, pending_suggestion)
            events.append(f"merge suggested with profile {pending_suggestion} (same number, different name)")
    profile = await _active(session, ident.profile_id)
    if profile.id != ident.profile_id:
        ident.profile_id = profile.id
    if msg.sender_name and msg.sender_name != ident.display_name:  # WhatsApp sends the current profile name
        ident.display_name = msg.sender_name

    last = ident.last_user_message_at
    if last is None or msg.timestamp > last:
        ident.last_user_message_at = msg.timestamp

    conv = (await session.execute(select(Conversation).where(
        Conversation.channel_identity_id == ident.id, Conversation.status != "closed")
        .order_by(Conversation.id.desc()).limit(1))).scalar_one_or_none()
    if conv is None:
        conv = Conversation(channel_identity_id=ident.id, profile_id=profile.id)
        session.add(conv)
    elif conv.profile_id != profile.id:
        conv.profile_id = profile.id
    await session.flush()
    return Resolved(ident, profile, conv, is_new, events, prev_user_message_at=last)


async def _find_owner_of(session: AsyncSession, kind: str, value: str, exclude_id: int) -> Profile | None:
    h = crypto.blind_index(value, kind)
    col = Profile.phone_hash if kind == "phone" else Profile.email_hash
    p = (await session.execute(select(Profile).where(col == h, Profile.merged_into_id.is_(None),
                                                     Profile.id != exclude_id))).scalar_one_or_none()
    if p is None:
        p = await _profile_with_index(session, kind, h, exclude_id)
    if p is None and kind == "phone":  # the number may only exist as a WhatsApp channel identity
        digits = value.lstrip("+")
        row = (await session.execute(select(ChannelIdentity).where(
            ChannelIdentity.channel == "whatsapp", ChannelIdentity.external_id == digits,
            ChannelIdentity.profile_id.is_not(None)))).scalar_one_or_none()
        if row and row.profile_id != exclude_id:
            p = await _active(session, row.profile_id)
            if p.id == exclude_id:
                p = None
    return p


async def handle_shared_contacts(session: AsyncSession, r: Resolved, text: str) -> None:
    """A phone/email in the message: only the guest's OWN is linked/merged/saved (contact_owner). A relative's goes on
    that relative, our own office's or anyone else's is ignored, and an unclear one is left to the AI
    (post_reply.collect_guest_info) unless we had just asked for their number."""
    from app.services import family as family_svc  # local import avoids a cycle
    expected = r.conversation.lead_awaiting_at is not None or contact_owner.mostly_contact(text)
    done: set[str] = set()
    for kind, value, who in await contact_owner.owners(session, text):
        if who.kind == "self" or (who.kind == "unknown" and expected):
            if kind not in done:  # one own phone / email per message
                done.add(kind)
                await save_own_contact(session, r, kind, value)
        elif who.kind == "family" and (relation := contact_owner.relation_en(who.relation)):
            status = await family_svc.upsert(session, r.profile, {"relation": relation, "details": {kind: value}})
            r.events.append(f"{kind} of the guest's {relation}: {status}")
        else:
            r.events.append(f"{kind} not the guest's ({who.kind}{': left to the AI' if who.kind == 'unknown' else ''})")


async def save_own_contact(session: AsyncSession, r: Resolved, kind: str, value: str) -> None:
    """The guest's own phone/email: link/merge with the profile that owns it, otherwise save it (or hold it
    encrypted until consent)."""
    policy = await get_setting(session, "merge_policy")
    owner = await _find_owner_of(session, kind, value, r.profile.id)
    await remember_contact(session, r.profile.id, kind, value)
    if owner is not None:
        same = _same_name(await _names(session, r.profile.id), await _names(session, owner.id))
        if policy == "suggest" and not same:
            await suggest_merge(session, r.profile.id, owner.id)
            r.events.append(f"merge suggested with profile {owner.id}")
            return
        try:
            await merge_profiles(session, keep_id=owner.id, merge_id=r.profile.id,
                                 actor="system", reason=f"shared_{kind}")
        except MergeError as exc:
            logger.warning("auto-merge skipped: %s", exc)
            return
        r.profile = await _active(session, owner.id)
        r.conversation.profile_id = r.profile.id
        r.events.append(f"merged into profile {owner.id} via shared {kind}")
        if r.profile.consent_given and (kind == "phone" and not r.profile.phone_hash or
                                        kind == "email" and not r.profile.email_hash):
            await consent_svc.apply_field(session, r.profile, kind, value)
        return
    if r.profile.consent_given:
        await consent_svc.apply_field(session, r.profile, kind, value)
        r.events.append(f"saved {kind}")
    elif r.profile.consent_at is None:  # not declined -> hold encrypted until consent
        await consent_svc.stash_pending(session, r.profile, kind, value)
        r.events.append(f"held {kind} pending consent")


async def save_contact_from_ai(session: AsyncSession, profile: Profile, kind: str, value: str) -> str:
    """The AI decided an unclear number is the guest's own: save it, but never merge on the AI's word alone
    (another profile with it only gets a merge suggestion for staff)."""
    owner = await _find_owner_of(session, kind, value, profile.id)
    await remember_contact(session, profile.id, kind, value)
    if owner is not None:
        await suggest_merge(session, profile.id, owner.id)
        return "merge suggested"
    if profile.consent_given:
        return await consent_svc.apply_field(session, profile, kind, value)
    if profile.consent_at is None:
        await consent_svc.stash_pending(session, profile, kind, value)
        return "pending"
    return "rejected"


# ---- one guest = one profile --------------------------------------------------------------------------------
async def _profile_with_index(session: AsyncSession, kind: str, idx: str, exclude_id: int) -> Profile | None:
    pid = (await session.execute(select(ProfileFact.profile_id).where(
        ProfileFact.key == f"merge_idx_{kind}", ProfileFact.value == idx, ProfileFact.profile_id != exclude_id)
        .order_by(ProfileFact.profile_id).limit(1))).scalar_one_or_none()
    if pid is None:
        return None
    p = await _active(session, pid)
    return None if p.id == exclude_id or p.erased_at else p


async def remember_contact(session: AsyncSession, profile_id: int, kind: str, value: str) -> None:
    """Keyed hash of a phone/email the guest gave, kept even before consent (it can't be read back), only to
    recognise the same guest on another channel."""
    from app.services.contacts import normalize_email
    v = normalize_phone(value) if kind == "phone" else normalize_email(value)
    if not v:
        return
    idx = crypto.blind_index(v, kind)
    f = (await session.execute(select(ProfileFact).where(
        ProfileFact.profile_id == profile_id, ProfileFact.key == f"merge_idx_{kind}"))).scalar_one_or_none()
    if f is None:
        session.add(ProfileFact(profile_id=profile_id, key=f"merge_idx_{kind}", value=idx))
    else:
        f.value = idx
    await session.flush()


async def _names(session: AsyncSession, profile_id: int) -> set[str]:
    p = await session.get(Profile, profile_id)
    rows = (await session.execute(select(ChannelIdentity.display_name).where(
        ChannelIdentity.profile_id == profile_id, ChannelIdentity.display_name.is_not(None),
        ~ChannelIdentity.external_id.like("test:%")))).scalars().all()
    return {n for n in (norm(x) for x in [*rows, p.name if p else None] if x) if n}


def _same_name(a: set[str], b: set[str]) -> bool:
    """Full names (2+ words) that are identical, ignoring case and punctuation."""
    return any(n in b for n in a if len(n.split()) >= 2)


async def suggest_merge(session: AsyncSession, profile_id: int, other_id: int) -> None:
    if profile_id == other_id:
        return
    f = (await session.execute(select(ProfileFact).where(
        ProfileFact.profile_id == profile_id, ProfileFact.key == "merge_suggestion"))).scalar_one_or_none()
    if f is None:
        session.add(ProfileFact(profile_id=profile_id, key="merge_suggestion", value=str(other_id)))
        await session.flush()


async def _merge_into(session: AsyncSession, r: Resolved, keep_id: int, reason: str) -> bool:
    keep_id = (await _active(session, keep_id)).id
    if keep_id == r.profile.id:
        return False
    try:
        await merge_profiles(session, keep_id=keep_id, merge_id=r.profile.id, actor="system", reason=reason)
    except MergeError as exc:
        logger.warning("auto-merge skipped: %s", exc)
        return False
    r.profile = await _active(session, keep_id)
    r.conversation.profile_id = r.profile.id
    r.events.append(f"merged into profile {keep_id} ({reason})")
    return True


async def link_same_person(session: AsyncSession, r: Resolved, adapter, creds) -> None:
    """Runs for a guest's first message on a channel identity (after their name/photo were fetched)."""
    if not r.is_new_identity or r.identity.external_id.startswith("test:"):
        return
    # 1) Meta-confirmed: this person's id on another Page of the same Business
    if creds is not None and r.identity.channel in ("messenger", "instagram"):
        try:
            others = await adapter.other_page_ids(r.identity.external_id, creds)
        except Exception as exc:  # noqa: BLE001 - needs the Pages and the app in one Business Manager
            logger.info("ids_for_pages unavailable: %s", exc)
            others = []
        if others:
            owner = (await session.execute(select(ChannelIdentity.profile_id).where(
                ChannelIdentity.channel == r.identity.channel, ChannelIdentity.external_id.in_(others),
                ChannelIdentity.profile_id.is_not(None), ChannelIdentity.id != r.identity.id)
                .order_by(ChannelIdentity.id).limit(1))).scalar_one_or_none()
            if owner is not None and await _merge_into(session, r, owner, "same_person_meta"):
                return
    # 2) the same full name on another identity: very likely the same guest on another Page -> suggest
    name = norm(r.identity.display_name or "")
    if len(name.split()) >= 2:
        other = (await session.execute(select(ChannelIdentity.profile_id).where(
            func.lower(ChannelIdentity.display_name) == (r.identity.display_name or "").lower(),
            ChannelIdentity.profile_id.is_not(None), ChannelIdentity.profile_id != r.profile.id,
            ~ChannelIdentity.external_id.like("test:%")).order_by(ChannelIdentity.id).limit(1))).scalar_one_or_none()
        if other is not None:
            other = (await _active(session, other)).id
            if other != r.profile.id:
                await suggest_merge(session, r.profile.id, other)
                r.events.append(f"merge suggested with profile {other} (same name)")
