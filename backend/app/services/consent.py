"""Consent state machine, safe field saving, and "delete my data"."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models import ChannelIdentity, Conversation, FamilyMember, Message, Profile, ProfileFact, ProfileMergeLog
from app.privacy.patterns import digits_only, luhn_ok

_YES = {"yes", "y", "yep", "yeah", "ok", "okay", "sure", "agree", "agreed", "haan", "ha", "ji", "hya", "hyan",
        "হ্যাঁ", "হ্যা", "হাঁ", "জি", "ঠিক আছে", "ঠিক", "রাজি", "অবশ্যই", "thik ache", "thik achhe", "hoy", "ঠিক আছে।"}
_NO = {"no", "n", "nope", "nah", "না", "না।", "লাগবে না", "দরকার নেই", "no thanks", "dont", "don't", "na", "nai"}
_ERASE_RE = re.compile(
    r"(?i)(delete|erase|remove|forget)\s+(all\s+)?(my\s+)?(personal\s+)?(data|info|information|details|me)\b|"
    r"আমার\s+(তথ্য|ডাটা|ডেটা)\s*(মুছে|ডিলিট|delete)|তথ্য\s+মুছে\s+(ফেল|দিন|দাও)|ডেটা\s+ডিলিট|"
    r"(?i:\b(amar|amr|my)\s+(sob\s+|shob\s+|all\s+)?(personal\s+)?(data|tottho|totho|info|details)\s+"
    r"(muche|muchhe|mucha|muse|delete|remove|erase|clear)\s*(kore\s+)?(din|dao|den|felun|fel|felen|koren|korun|please)?\b)")

PERSONAL_SCALARS = ("name", "location", "age", "profession", "language")
CONTACT_FIELDS = ("phone", "email")
_BLOCKED_KEY_PARTS = ("password", "passcode", "pin", "otp", "cvv", "cvc", "card", "secret", "token", "nid", "ssn")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def classify_consent_reply(text: str) -> str | None:
    t = re.sub(r"[.!?,।]+$", "", text.strip().lower())
    if not t or len(t.split()) > 4:
        return None
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None


def is_erasure_request(text: str) -> bool:
    return bool(_ERASE_RE.search(text))


def consent_state(p: Profile) -> str:
    if p.consent_given:
        return "given"
    if p.consent_at is not None:
        return "declined"
    return "asked" if p.consent_asked_at is not None else "unknown"


def mark_consent_asked(p: Profile) -> None:
    if p.consent_asked_at is None:
        p.consent_asked_at = datetime.now(timezone.utc)


async def record_consent(session: AsyncSession, p: Profile, given: bool) -> None:
    p.consent_given = given
    p.consent_at = datetime.now(timezone.utc)
    if given:
        await promote_pending(session, p)
    else:
        await session.execute(delete(ProfileFact).where(ProfileFact.profile_id == p.id,
                                                        ProfileFact.key.like("pending\\_%", escape="\\")))
    await session.flush()


# ---- phone / email storage ------------------------------------------------------------------
def set_phone(p: Profile, e164: str) -> None:
    p.phone_hash = crypto.blind_index(e164, "phone")
    p.phone_enc = crypto.encrypt(e164, aad="phone")
    p.summary_dirty = True


def set_email(p: Profile, email: str) -> None:
    p.email_hash = crypto.blind_index(email, "email")
    p.email_enc = crypto.encrypt(email, aad="email")
    p.summary_dirty = True


def get_phone(p: Profile) -> str | None:
    return crypto.decrypt(p.phone_enc, aad="phone") if p.phone_enc else None


def get_email(p: Profile) -> str | None:
    return crypto.decrypt(p.email_enc, aad="email") if p.email_enc else None


async def _upsert_fact(session: AsyncSession, profile_id: int, key: str, value: str, confidence: float = 1.0,
                       source_message_id: int | None = None, source: str | None = None) -> bool:
    """Returns False when the write was skipped: the AI never overwrites a value staff entered."""
    fact = (await session.execute(select(ProfileFact).where(ProfileFact.profile_id == profile_id,
                                                            ProfileFact.key == key))).scalar_one_or_none()
    if fact is None:
        session.add(ProfileFact(profile_id=profile_id, key=key, value=value, confidence=confidence,
                                source_message_id=source_message_id, source=source))
    elif fact.source == "staff" and source == "ai" and fact.value != value:
        return False
    else:
        fact.value, fact.confidence, fact.source_message_id, fact.source = value, confidence, source_message_id, source
    await session.flush()
    return True


async def stash_pending(session: AsyncSession, p: Profile, key: str, value: str) -> None:
    """Hold a volunteered value (encrypted) until the user consents to saving personal data."""
    await _upsert_fact(session, p.id, f"pending_{key}", crypto.encrypt(value, aad=f"pending_{key}"))


async def promote_pending(session: AsyncSession, p: Profile) -> None:
    facts = (await session.execute(select(ProfileFact).where(
        ProfileFact.profile_id == p.id, ProfileFact.key.like("pending\\_%", escape="\\")))).scalars().all()
    for f in facts:
        key = f.key[len("pending_"):]
        try:
            value = crypto.decrypt(f.value, aad=f.key)
        except Exception:  # noqa: BLE001
            await session.delete(f)
            continue
        await session.delete(f)
        await session.flush()
        await apply_field(session, p, key, value, consent_checked=True)


def _valid_scalar(key: str, value) -> object | None:
    if key == "age":
        try:
            n = int(str(value).strip())
        except ValueError:
            return None
        return n if 5 <= n <= 110 else None
    if key == "language":
        v = str(value).strip().lower()[:16]
        return v or None
    v = re.sub(r"\s+", " ", str(value)).strip()[:200]
    return v or None


def _looks_sensitive(key: str, value: str) -> bool:
    if any(part in key.lower() for part in _BLOCKED_KEY_PARTS):
        return True
    if key in CONTACT_FIELDS:  # validated as a phone/email below; a 13-digit +880 number can pass the card checksum
        return False
    d = digits_only(value)
    return 13 <= len(d) <= 19 and luhn_ok(d)


async def apply_field(session: AsyncSession, p: Profile, key: str, value, *, consent_checked: bool = False,
                      confidence: float = 1.0, source_message_id: int | None = None, source: str = "ai") -> str:
    """Validate and save one field (extracted by the AI or typed by staff). Returns: saved | pending | rejected | kept."""
    from app.services.profile_fields import PERSONAL_FACT_KEYS, STAFF_ONLY_KEYS  # local import avoids a cycle
    key = key.strip().lower()
    if value is None or str(value).strip() == "" or _looks_sensitive(key, str(value)):
        return "rejected"
    if key in STAFF_ONLY_KEYS and source != "staff":
        return "rejected"
    is_personal = key in PERSONAL_SCALARS or key in CONTACT_FIELDS or key in PERSONAL_FACT_KEYS
    if is_personal and not (p.consent_given or consent_checked):
        if p.consent_at is not None:  # explicitly declined: never keep
            return "rejected"
        await stash_pending(session, p, key, str(value))
        return "pending"
    if key in CONTACT_FIELDS:
        from app.services.contacts import normalize_email, normalize_phone  # local import avoids a cycle
        norm = normalize_phone(str(value)) if key == "phone" else normalize_email(str(value))
        if not norm:
            return "rejected"
        set_phone(p, norm) if key == "phone" else set_email(p, norm)
    elif key in PERSONAL_SCALARS:
        v = _valid_scalar(key, value)
        if v is None:
            return "rejected"
        setattr(p, key, v)
        p.summary_dirty = True
    else:
        if not _KEY_RE.match(key):
            return "rejected"
        if not await _upsert_fact(session, p.id, key, str(value).strip()[:300], confidence, source_message_id, source):
            return "kept"
        p.summary_dirty = True
    await session.flush()
    return "saved"


# ---- "delete my data" -------------------------------------------------------------------------
async def _tree_ids(session: AsyncSession, root_id: int) -> list[int]:
    ids, frontier = [root_id], [root_id]
    while frontier:
        rows = (await session.execute(select(Profile.id).where(Profile.merged_into_id.in_(frontier)))).scalars().all()
        frontier = [r for r in rows if r not in ids]
        ids.extend(frontier)
    return ids


async def erase_profile(session: AsyncSession, profile_id: int) -> None:
    """Wipe personal data of a profile and every profile merged into it; keep channel identities so the
    chat can continue anonymously. Message text is replaced, merge snapshots are deleted."""
    ids = await _tree_ids(session, profile_id)
    now = datetime.now(timezone.utc)
    await session.execute(delete(ProfileFact).where(ProfileFact.profile_id.in_(ids)))
    await session.execute(delete(FamilyMember).where(FamilyMember.profile_id.in_(ids)))
    await session.execute(delete(ProfileMergeLog).where(
        (ProfileMergeLog.kept_profile_id.in_(ids)) | (ProfileMergeLog.merged_profile_id.in_(ids))))
    await session.execute(update(Message).where(Message.conversation_id.in_(
        select(Conversation.id).where(Conversation.profile_id.in_(ids)))).values(
        content_redacted="[deleted at user's request]", sources_json=None))
    await session.execute(update(Conversation).where(Conversation.profile_id.in_(ids)).values(summary=None))
    await session.execute(update(Profile).where(Profile.id.in_(ids)).values(
        name=None, phone_hash=None, phone_enc=None, email_hash=None, email_enc=None, location=None, age=None,
        profession=None, language=None, summary=None, summary_dirty=False, consent_given=False,
        consent_at=now, erased_at=now))
    # Re-point merged children to the root so they are not left dangling.
    await session.execute(update(ChannelIdentity).where(ChannelIdentity.profile_id.in_(ids)).values(profile_id=profile_id))
    await session.execute(update(Conversation).where(Conversation.profile_id.in_(ids)).values(profile_id=profile_id))
    await session.flush()
