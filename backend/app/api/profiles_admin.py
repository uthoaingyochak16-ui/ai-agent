from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.core import crypto
from app.db.session import get_session
from app.models import (AdminUser, ChannelIdentity, Conversation, Message, Profile, ProfileFact, ProfileMergeLog)
from app.services import consent as C
from app.services.contacts import normalize_email, normalize_phone
from app.services.merge import MergeError, merge_profiles, unmerge_profiles
from app.services.post_reply import refresh_profile_summary
from app.services.profile_fields import catalog_json, describe

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def mask_phone(v: str | None) -> str | None:
    return None if not v else v[:4] + "*" * max(0, len(v) - 7) + v[-3:]


def mask_email(v: str | None) -> str | None:
    if not v or "@" not in v:
        return v
    local, domain = v.split("@", 1)
    return local[:1] + "***@" + domain


def _contact(p: Profile, user: AdminUser) -> tuple[str | None, str | None]:
    ph, em = C.get_phone(p), C.get_email(p)
    return (ph, em) if user.role == "admin" else (mask_phone(ph), mask_email(em))


async def _channels(session: AsyncSession, pid: int) -> list[str]:
    return list((await session.execute(select(ChannelIdentity.channel).where(ChannelIdentity.profile_id == pid).distinct())).scalars())


async def _channel_face(session: AsyncSession, pid: int) -> tuple[str | None, str | None]:
    """Name and photo the guest uses on Messenger / WhatsApp (most recently active channel first)."""
    rows = (await session.execute(select(ChannelIdentity.display_name, ChannelIdentity.avatar_url).where(
        ChannelIdentity.profile_id == pid, ~ChannelIdentity.external_id.like("test:%"))
        .order_by(ChannelIdentity.last_user_message_at.desc().nullslast()))).all()
    name = next((n for n, _ in rows if n), None)
    return name, next((a for _, a in rows if a), None)


def _summary_row(p: Profile, user: AdminUser, channels: list[str], face: tuple[str | None, str | None] = (None, None)) -> dict:
    ph, em = _contact(p, user)
    return {"id": p.id, "name": p.name, "channel_name": face[0], "avatar_url": face[1],
            "display_name": p.name or face[0], "phone": ph, "email": em, "location": p.location, "age": p.age,
            "profession": p.profession, "language": p.language, "consent": C.consent_state(p), "channels": channels,
            "created_at": p.created_at, "updated_at": p.updated_at}


@router.get("")
async def list_profiles(q: str | None = None, channel: str | None = None, limit: int = Query(50, le=200), offset: int = 0,
                        include_test: bool = False,
                        user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    stmt = select(Profile).where(Profile.merged_into_id.is_(None))
    if not include_test:  # profiles that only ever talked through Test Chat are not guests
        real = select(ChannelIdentity.profile_id).where(~ChannelIdentity.external_id.like("test:%"))
        test = select(ChannelIdentity.profile_id).where(ChannelIdentity.external_id.like("test:%"))
        stmt = stmt.where(or_(Profile.id.in_(real), ~Profile.id.in_(test)))
    if q and q.strip():
        s = q.strip()
        conds = [Profile.name.ilike(f"%{s}%"), Profile.id.in_(select(ChannelIdentity.profile_id).where(
            ChannelIdentity.display_name.ilike(f"%{s}%")))]
        ph = normalize_phone(s)
        if ph:
            conds.append(Profile.phone_hash == crypto.blind_index(ph, "phone"))
        if "@" in s:
            conds.append(Profile.email_hash == crypto.blind_index(s.lower(), "email"))
        stmt = stmt.where(or_(*conds))
    if channel:
        stmt = stmt.where(Profile.id.in_(select(ChannelIdentity.profile_id).where(ChannelIdentity.channel == channel)))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await session.execute(stmt.order_by(Profile.updated_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [_summary_row(p, user, await _channels(session, p.id), await _channel_face(session, p.id))
                                      for p in rows]}


class ProfileIn(BaseModel):
    name: str | None = Field(None, max_length=160)
    phone: str | None = None
    email: str | None = None
    location: str | None = Field(None, max_length=200)
    age: int | None = Field(None, ge=5, le=110)
    profession: str | None = Field(None, max_length=160)
    language: str | None = Field(None, max_length=16)
    consent_given: bool | None = None


async def _apply(session: AsyncSession, p: Profile, body: ProfileIn, fields_set: set[str]) -> None:
    if "consent_given" in fields_set and body.consent_given is not None:
        await C.record_consent(session, p, body.consent_given)
    for k in ("name", "location", "age", "profession", "language"):
        if k in fields_set:
            v = getattr(body, k)
            if v in (None, ""):
                setattr(p, k, None)
            else:
                if await C.apply_field(session, p, k, v, consent_checked=True) != "saved":
                    raise HTTPException(422, f"Invalid {k}")
    for k, norm in (("phone", normalize_phone), ("email", normalize_email)):
        if k not in fields_set:
            continue
        v = getattr(body, k)
        if v in (None, ""):
            setattr(p, f"{k}_hash", None), setattr(p, f"{k}_enc", None)
            continue
        n = norm(v)
        if not n:
            raise HTTPException(422, f"Invalid {k}")
        col = Profile.phone_hash if k == "phone" else Profile.email_hash
        owner = (await session.execute(select(Profile.id).where(col == crypto.blind_index(n, k), Profile.id != p.id))).scalar_one_or_none()
        if owner:
            raise HTTPException(409, {"error": "duplicate", "field": k, "owner_profile_id": owner})
        (C.set_phone if k == "phone" else C.set_email)(p, n)
    p.summary_dirty = True
    await session.flush()


@router.post("")
async def create_profile(body: ProfileIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = Profile()
    session.add(p)
    await session.flush()
    try:
        await _apply(session, p, body, body.model_fields_set)
    except HTTPException:
        await session.rollback()
        raise
    await session.commit()
    await session.refresh(p)
    return _summary_row(p, user, [])


@router.get("/fields")
async def fields(_: AdminUser = Depends(any_staff)):
    return catalog_json()


@router.get("/merge-log")
async def merge_log(session: AsyncSession = Depends(get_session), _: AdminUser = Depends(admin_only)):
    rows = (await session.execute(select(ProfileMergeLog).order_by(ProfileMergeLog.id.desc()).limit(200))).scalars().all()
    return [{"id": r.id, "kept": r.kept_profile_id, "merged": r.merged_profile_id, "reason": r.reason,
             "actor": r.actor, "created_at": r.created_at, "reverted_at": r.reverted_at} for r in rows]


@router.get("/merge-suggestions")
async def suggestions(session: AsyncSession = Depends(get_session), _: AdminUser = Depends(admin_only)):
    rows = (await session.execute(select(ProfileFact).where(ProfileFact.key == "merge_suggestion"))).scalars().all()
    out = []
    for f in rows:
        a, b = await session.get(Profile, f.profile_id), await session.get(Profile, int(f.value))
        if not a or not b or a.id == b.id or a.merged_into_id or b.merged_into_id:  # stale: already merged
            await session.delete(f)
            continue
        out.append({"profile_id": a.id, "suggested_with": b.id,
                    "names": [(await _channel_face(session, a.id))[0] or a.name, (await _channel_face(session, b.id))[0] or b.name]})
    await session.commit()
    return out


@router.delete("/merge-suggestions/{profile_id}")
async def dismiss_suggestion(profile_id: int, session: AsyncSession = Depends(get_session), _: AdminUser = Depends(admin_only)):
    """Not the same person: forget the suggestion."""
    await session.execute(ProfileFact.__table__.delete().where(ProfileFact.profile_id == profile_id,
                                                               ProfileFact.key == "merge_suggestion"))
    await session.commit()
    return {"ok": True}


class MergeIn(BaseModel):
    keep_id: int
    merge_id: int


@router.post("/merge")
async def merge(body: MergeIn, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    try:
        log = await merge_profiles(session, body.keep_id, body.merge_id, actor=user.email, reason="manual")
    except MergeError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.execute(ProfileFact.__table__.delete().where(ProfileFact.key == "merge_suggestion",
                          ProfileFact.profile_id.in_([body.keep_id, body.merge_id])))
    await session.commit()
    return {"merge_log_id": log.id}


@router.post("/merge-log/{log_id}/unmerge")
async def unmerge(log_id: int, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    try:
        warnings = await unmerge_profiles(session, log_id, actor=user.email)
    except MergeError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    return {"ok": True, "warnings": warnings}


@router.get("/export.csv")
async def export_csv(session: AsyncSession = Depends(get_session), _: AdminUser = Depends(admin_only)):
    from app.api.qa import _safe_cell
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "phone", "email", "location", "age", "profession", "language", "consent", "channels", "created_at"])
    for p in (await session.execute(select(Profile).where(Profile.merged_into_id.is_(None), Profile.erased_at.is_(None)).order_by(Profile.id))).scalars():
        w.writerow([p.id, _safe_cell(p.name or ""), C.get_phone(p) or "", C.get_email(p) or "", _safe_cell(p.location or ""), p.age or "",
                    _safe_cell(p.profession or ""), p.language or "", C.consent_state(p), "|".join(await _channels(session, p.id)), p.created_at.isoformat()])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"content-disposition": 'attachment; filename="profiles.csv"'})


async def _get(session: AsyncSession, pid: int) -> Profile:
    p = await session.get(Profile, pid)
    if not p:
        raise HTTPException(404, "Not found")
    return p


@router.get("/{pid}")
async def get_profile(pid: int, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = await _get(session, pid)
    idents = (await session.execute(select(ChannelIdentity).where(ChannelIdentity.profile_id == pid))).scalars().all()
    facts = (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == pid).order_by(ProfileFact.key))).scalars().all()
    convs = (await session.execute(select(Conversation).where(Conversation.profile_id == pid).order_by(Conversation.id.desc()))).scalars().all()
    children = (await session.execute(select(ProfileMergeLog).where(ProfileMergeLog.kept_profile_id == pid).order_by(ProfileMergeLog.id.desc()))).scalars().all()
    d = _summary_row(p, user, sorted({i.channel for i in idents}), await _channel_face(session, pid))
    d.update({"summary": p.summary, "merged_into_id": p.merged_into_id, "erased_at": p.erased_at,
              "identities": [{"id": i.id, "channel": i.channel, "display_name": i.display_name, "avatar_url": i.avatar_url, "last_user_message_at": i.last_user_message_at,
                              "is_test": i.external_id.startswith("test:")} for i in idents],
              "facts": [{"key": f.key, "value": f.value, "confidence": f.confidence, "updated_at": f.updated_at,
                         "source": f.source or ("ai" if f.source_message_id else None), **describe(f.key)}
                        for f in facts if not f.key.startswith(("pending_", "merge_"))],
              "pending_fields": [f.key[8:] for f in facts if f.key.startswith("pending_")],
              "merge_suggestion": next((int(f.value) for f in facts if f.key == "merge_suggestion"), None),
              "conversations": [{"id": c.id, "channel_identity_id": c.channel_identity_id, "status": c.status, "ai_paused": c.ai_paused,
                                 "last_message_at": c.last_message_at} for c in convs],
              "merges": [{"id": m.id, "merged_profile_id": m.merged_profile_id, "reason": m.reason, "actor": m.actor,
                          "created_at": m.created_at, "reverted_at": m.reverted_at} for m in children]})
    return d


@router.patch("/{pid}")
async def patch_profile(pid: int, body: ProfileIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = await _get(session, pid)
    if p.merged_into_id or p.erased_at:
        raise HTTPException(409, "Profile is merged or erased")
    try:
        await _apply(session, p, body, body.model_fields_set)
    except HTTPException:
        await session.rollback()
        raise
    await session.commit()
    await session.refresh(p)
    return _summary_row(p, user, await _channels(session, pid))


class FactIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    value: str = Field(min_length=1, max_length=300)


@router.put("/{pid}/facts")
async def put_fact(pid: int, body: FactIn, user: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Staff adds or corrects a detail. Staff values are kept even if the AI later extracts something else."""
    p = await _get(session, pid)
    if p.merged_into_id or p.erased_at:
        raise HTTPException(409, "Profile is merged or erased")
    key = re.sub(r"[^a-z0-9]+", "_", body.key.strip().lower()).strip("_")
    if key in C.PERSONAL_SCALARS or key in C.CONTACT_FIELDS:
        raise HTTPException(422, f"Edit '{key}' in the details form")
    if key.startswith(("pending_", "merge_")):
        raise HTTPException(422, "Reserved key")
    res = await C.apply_field(session, p, key, body.value, consent_checked=True, source="staff")
    if res != "saved":
        raise HTTPException(422, "This value can't be saved (sensitive data such as card numbers or passwords is blocked)")
    await refresh_profile_summary(session, p)
    await session.commit()
    return {"ok": True, "key": key}


@router.delete("/{pid}/facts/{key}")
async def delete_fact(pid: int, key: str, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    p = await _get(session, pid)
    await session.execute(ProfileFact.__table__.delete().where(ProfileFact.profile_id == pid, ProfileFact.key == key,
                                                               ~ProfileFact.key.like(r"merge\_%", escape="\\")))
    p.summary_dirty = True
    await refresh_profile_summary(session, p)
    await session.commit()
    return {"ok": True}


@router.delete("/{pid}")
async def delete_profile(pid: int, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    await _get(session, pid)
    await C.erase_profile(session, pid)
    await session.commit()
    return {"ok": True}


@router.get("/{pid}/messages")
async def profile_messages(pid: int, limit: int = Query(300, le=1000), session: AsyncSession = Depends(get_session), _: AdminUser = Depends(any_staff)):
    await _get(session, pid)
    rows = (await session.execute(
        select(Message, ChannelIdentity.channel).join(Conversation, Message.conversation_id == Conversation.id)
        .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
        .where(Conversation.profile_id == pid).order_by(Message.created_at, Message.id).limit(limit))).all()
    return [{"id": m.id, "channel": ch, "role": m.role, "content": m.content_redacted, "created_at": m.created_at,
             "conversation_id": m.conversation_id, "delivery_status": m.delivery_status, "flag": m.flag} for m, ch in rows]


@router.get("/{pid}/export")
async def export_profile(pid: int, session: AsyncSession = Depends(get_session), user: AdminUser = Depends(admin_only)):
    d = await get_profile(pid, user, session)
    d["messages"] = await profile_messages(pid, 1000, session, user)
    return d
