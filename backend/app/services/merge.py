"""Safe profile merge / unmerge with an audit snapshot."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelIdentity, Conversation, FamilyMember, Profile, ProfileFact, ProfileMergeLog

SCALARS = ("name", "location", "age", "profession", "language")
SECRETS = {"phone": ("phone_hash", "phone_enc"), "email": ("email_hash", "email_enc")}


class MergeError(Exception):
    pass


async def _lock_pair(session: AsyncSession, a: int, b: int) -> dict[int, Profile]:
    rows = (await session.execute(select(Profile).where(Profile.id.in_([a, b])).order_by(Profile.id)
                                  .with_for_update())).scalars().all()
    got = {p.id: p for p in rows}
    if a not in got or b not in got:
        raise MergeError("profile not found")
    return got


async def merge_profiles(session: AsyncSession, keep_id: int, merge_id: int, *, actor: str = "system",
                         reason: str = "manual") -> ProfileMergeLog:
    """Merge `merge_id` INTO `keep_id`. Newest value wins on conflict. Nothing is deleted: the merged
    profile is retained (marked merged_into_id) so the merge can be undone from the snapshot."""
    if keep_id == merge_id:
        raise MergeError("cannot merge a profile into itself")
    got = await _lock_pair(session, keep_id, merge_id)
    keep, mrg = got[keep_id], got[merge_id]
    if keep.merged_into_id or mrg.merged_into_id:
        raise MergeError("one of the profiles is already merged")
    if keep.erased_at or mrg.erased_at:
        raise MergeError("cannot merge an erased profile")
    mrg_newer = (mrg.updated_at or datetime.min.replace(tzinfo=timezone.utc)) > (
        keep.updated_at or datetime.min.replace(tzinfo=timezone.utc))

    snap: dict = {"keep_changed": {}, "merged_before": {}, "identity_ids": [], "conversation_ids": [],
                  "moved_fact_ids": [], "overwritten_facts": {}}

    # scalar fields: fill gaps, newest wins on conflict
    for f in SCALARS:
        kv, mv = getattr(keep, f), getattr(mrg, f)
        if mv is not None and (kv is None or (kv != mv and mrg_newer)):
            snap["keep_changed"][f] = {"old": kv, "new": mv}
            setattr(keep, f, mv)

    # phone / email: clear merged hashes first (unique index), then give keep the winner
    for label, (h, e) in SECRETS.items():
        mh, me, kh, ke = getattr(mrg, h), getattr(mrg, e), getattr(keep, h), getattr(keep, e)
        if mh is None:
            continue
        snap["merged_before"][label] = {"hash": mh, "enc": me}
        setattr(mrg, h, None)
        setattr(mrg, e, None)
        await session.flush()
        if kh is None or (kh != mh and mrg_newer):
            snap["keep_changed"][label] = {"old_hash": kh, "old_enc": ke, "new_hash": mh}
            setattr(keep, h, mh)
            setattr(keep, e, me)
            await session.flush()

    # consent: either side's consent covers the combined data
    if mrg.consent_given and not keep.consent_given:
        snap["keep_changed"]["consent"] = {"old_given": keep.consent_given,
                                           "old_at": keep.consent_at.isoformat() if keep.consent_at else None}
        keep.consent_given, keep.consent_at = True, mrg.consent_at

    # identities and conversations follow the profile
    ids = (await session.execute(select(ChannelIdentity.id).where(ChannelIdentity.profile_id == merge_id))).scalars().all()
    snap["identity_ids"] = list(ids)
    await session.execute(update(ChannelIdentity).where(ChannelIdentity.profile_id == merge_id).values(profile_id=keep_id))
    cids = (await session.execute(select(Conversation.id).where(Conversation.profile_id == merge_id))).scalars().all()
    snap["conversation_ids"] = list(cids)
    await session.execute(update(Conversation).where(Conversation.profile_id == merge_id).values(profile_id=keep_id))

    # facts: move new keys, resolve conflicts by newest
    keep_facts = {f.key: f for f in (await session.execute(
        select(ProfileFact).where(ProfileFact.profile_id == keep_id))).scalars()}
    for mf in (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == merge_id))).scalars().all():
        kf = keep_facts.get(mf.key)
        if kf is None:
            mf.profile_id = keep_id
            snap["moved_fact_ids"].append(mf.id)
        elif mf.updated_at > kf.updated_at:
            snap["overwritten_facts"][mf.key] = {"fact_id": kf.id, "old_value": kf.value,
                                                 "old_confidence": kf.confidence, "new_value": mf.value}
            kf.value, kf.confidence = mf.value, mf.confidence

    # relatives follow the profile (two "mother" rows can be tidied up by staff)
    fids = (await session.execute(select(FamilyMember.id).where(FamilyMember.profile_id == merge_id))).scalars().all()
    snap["moved_family_ids"] = list(fids)
    await session.execute(update(FamilyMember).where(FamilyMember.profile_id == merge_id).values(profile_id=keep_id))

    mrg.merged_into_id = keep_id
    keep.summary_dirty = True
    log = ProfileMergeLog(kept_profile_id=keep_id, merged_profile_id=merge_id, snapshot=snap, reason=reason, actor=actor)
    session.add(log)
    await session.flush()
    return log


async def unmerge_profiles(session: AsyncSession, log_id: int, *, actor: str = "system") -> list[str]:
    """Undo a merge. Returns warnings for anything that could not be restored safely."""
    log = (await session.execute(select(ProfileMergeLog).where(ProfileMergeLog.id == log_id)
                                 .with_for_update())).scalar_one_or_none()
    if log is None:
        raise MergeError("merge log not found")
    if log.reverted_at is not None:
        raise MergeError("already reverted")
    got = await _lock_pair(session, log.kept_profile_id, log.merged_profile_id)
    keep, mrg = got[log.kept_profile_id], got[log.merged_profile_id]
    if mrg.merged_into_id != keep.id:
        raise MergeError("profile is no longer merged into this one")
    snap, warnings = log.snapshot, []

    await session.execute(update(ChannelIdentity).where(ChannelIdentity.id.in_(snap["identity_ids"] or [0]),
                          ChannelIdentity.profile_id == keep.id).values(profile_id=mrg.id))
    await session.execute(update(Conversation).where(Conversation.id.in_(snap["conversation_ids"] or [0]),
                          Conversation.profile_id == keep.id).values(profile_id=mrg.id))
    await session.execute(update(ProfileFact).where(ProfileFact.id.in_(snap["moved_fact_ids"] or [0]),
                          ProfileFact.profile_id == keep.id).values(profile_id=mrg.id))
    await session.execute(update(FamilyMember).where(FamilyMember.id.in_(snap.get("moved_family_ids") or [0]),
                          FamilyMember.profile_id == keep.id).values(profile_id=mrg.id))
    for key, o in snap["overwritten_facts"].items():
        fact = await session.get(ProfileFact, o["fact_id"])
        if fact and fact.profile_id == keep.id and fact.value == o["new_value"]:
            fact.value, fact.confidence = o["old_value"], o["old_confidence"]
        else:
            warnings.append(f"fact '{key}' changed after the merge; left as is")

    for f in SCALARS:
        ch = snap["keep_changed"].get(f)
        if ch:
            if getattr(keep, f) == ch["new"]:
                setattr(keep, f, ch["old"])
            else:
                warnings.append(f"{f} changed after the merge; left as is")
    for label, (h, e) in SECRETS.items():
        ch = snap["keep_changed"].get(label)
        if ch:
            if getattr(keep, h) == ch["new_hash"]:
                setattr(keep, h, ch["old_hash"])
                setattr(keep, e, ch["old_enc"])
                await session.flush()
            else:
                warnings.append(f"{label} changed after the merge; left as is")
    cons = snap["keep_changed"].get("consent")
    if cons and keep.consent_given:
        keep.consent_given = cons["old_given"]
        keep.consent_at = datetime.fromisoformat(cons["old_at"]) if cons["old_at"] else None
    for label, (h, e) in SECRETS.items():
        mb = snap["merged_before"].get(label)
        if not mb:
            continue
        taken = (await session.execute(select(Profile.id).where(getattr(Profile, h) == mb["hash"]))).first()
        if taken:
            warnings.append(f"{label} is now held by another profile; not restored on the unmerged profile")
        else:
            setattr(mrg, h, mb["hash"])
            setattr(mrg, e, mb["enc"])
            await session.flush()

    mrg.merged_into_id = None
    keep.summary_dirty = mrg.summary_dirty = True
    log.reverted_at, log.reverted_by = datetime.now(timezone.utc), actor
    await session.flush()
    return warnings
