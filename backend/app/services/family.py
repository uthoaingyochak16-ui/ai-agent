"""Relatives the guest talks about, one row per person (models.FamilyMember).

The AI extractor (post_reply.extract_profile) and staff both write here. A mention is matched to an existing row by
relation + name; "amar ma" without a name joins the one mother already known. The AI fills gaps but never changes
what staff entered. Like the guest's own details, nothing is kept without consent.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FamilyMember, Profile
from app.services import consent as consent_svc

# relations a person has only one of: a mention without a name is the same person
SINGLE = frozenset({"mother", "father", "husband", "wife", "spouse", "grandfather", "grandmother"})
_ALIASES = {"mom": "mother", "mum": "mother", "ma": "mother", "amma": "mother", "dad": "father", "baba": "father",
            "abba": "father", "bhai": "brother", "boner": "sister", "bon": "sister", "apu": "sister", "chele": "son",
            "meye": "daughter", "stri": "wife", "bou": "wife", "shami": "husband", "swami": "husband", "child": "child",
            "nana": "grandfather", "dada": "grandfather", "nani": "grandmother", "dadi": "grandmother"}
_BLOOD = re.compile(r"^\s*(AB|A|B|O)\s*(\+|-|positive|negative|pos|neg)\s*(ve)?\s*$", re.IGNORECASE)
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_DETAILS = 20


def relation_of(raw) -> str | None:
    r = re.sub(r"[^a-z ]+", "", str(raw or "").lower()).strip()
    r = _ALIASES.get(r, r)
    return r[:40] if r else None


def blood_group(raw) -> str | None:
    m = _BLOOD.match(str(raw or ""))
    if not m:
        return None
    sign = m.group(2).lower()
    return m.group(1).upper() + ("-" if sign.startswith(("-", "n")) else "+")


def _age(raw) -> int | None:
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 120 else None


def _same_name(a: str | None, b: str | None) -> bool:
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()  # noqa: E731
    return bool(a and b) and (norm(a) == norm(b) or norm(a).split()[0] == norm(b).split()[0])


async def _find(session: AsyncSession, profile_id: int, relation: str, name: str | None) -> FamilyMember | None:
    rows = (await session.execute(select(FamilyMember).where(
        FamilyMember.profile_id == profile_id, FamilyMember.relation == relation).order_by(FamilyMember.id))).scalars().all()
    if name:
        named = next((m for m in rows if _same_name(m.name, name)), None)
        if named:
            return named
        unnamed = [m for m in rows if not m.name]
        return unnamed[0] if len(unnamed) == 1 else (rows[0] if relation in SINGLE and len(rows) == 1 else None)
    return rows[0] if len(rows) == 1 or (rows and relation in SINGLE) else None


async def upsert(session: AsyncSession, p: Profile, data: dict, *, source: str = "ai") -> str:
    """Save one relative from {"relation", "name", "age", "blood_group", "details": {...}}.
    Returns created | updated | unchanged | rejected | no_consent."""
    relation = relation_of(data.get("relation"))
    if not relation:
        return "rejected"
    if source == "ai" and not p.consent_given:
        return "no_consent"
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:160] or None
    age, bg = _age(data.get("age")), blood_group(data.get("blood_group"))
    details = {}
    for k, v in (data.get("details") or {}).items() if isinstance(data.get("details"), dict) else ():
        k = re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_")
        v = re.sub(r"\s+", " ", str(v or "")).strip()[:200]
        if _KEY.match(k) and v and v.lower() not in ("null", "none") and not consent_svc._looks_sensitive(k, v):
            details[k] = v
    if not (name or age is not None or bg or details):
        return "rejected"  # "amar ma" alone tells us nothing to keep

    m = await _find(session, p.id, relation, name)
    if m is None:
        session.add(FamilyMember(profile_id=p.id, relation=relation, name=name, age=age, blood_group=bg,
                                 details=dict(list(details.items())[:MAX_DETAILS]), source=source))
        p.summary_dirty = True
        await session.flush()
        return "created"
    keep = m.source == "staff" and source == "ai"  # the AI only fills what staff left empty
    changed = False
    for attr, v in (("name", name), ("age", age), ("blood_group", bg)):
        if v is not None and getattr(m, attr) != v and not (keep and getattr(m, attr) is not None):
            setattr(m, attr, v)
            changed = True
    merged = dict(m.details or {})
    for k, v in details.items():
        if merged.get(k) != v and not (keep and k in merged) and (k in merged or len(merged) < MAX_DETAILS):
            merged[k] = v
            changed = True
    if changed:
        m.details = merged
        if source == "staff":
            m.source = "staff"
        p.summary_dirty = True
        await session.flush()
    return "updated" if changed else "unchanged"


def as_dict(m: FamilyMember) -> dict:
    return {"id": m.id, "relation": m.relation, "name": m.name, "age": m.age, "blood_group": m.blood_group,
            "details": m.details or {}, "source": m.source, "updated_at": m.updated_at}
