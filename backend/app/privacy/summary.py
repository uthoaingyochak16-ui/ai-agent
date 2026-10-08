"""Deterministic profile summary + missing-field hint. These two strings are ALL the AI ever sees of a profile.

Phone and email are never part of the summary. `never_send` lets the admin hide further fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.privacy.redaction import Redactor

COLLECTABLE = ("name", "phone", "email", "location", "age", "profession")
SUMMARY_MAX_CHARS = 420  # roughly 100 tokens
SAFE_FACT_KEYS_MAX = 5


@dataclass(frozen=True)
class ProfileView:
    """Plain data the summary may be built from. Deliberately has no phone/email fields."""
    name: str | None = None
    location: str | None = None
    age: int | None = None
    profession: str | None = None
    language: str | None = None
    facts: tuple[tuple[str, str], ...] = ()
    has_phone: bool = False        # stored or pending: used only to avoid asking again
    has_email: bool = False
    consent_state: str = "unknown"  # unknown | asked | given | declined
    pending: frozenset[str] = frozenset()  # fields volunteered but held until consent (don't ask again)


class PlainFormatter:
    """Pass-through used ONLY for the copy of the summary shown in the dashboard (never sent to a provider)."""
    def name_for_prompt(self, name: str | None) -> str | None:
        return name

    def redact(self, text: str) -> str:
        return text


def compose_summary(view: ProfileView, redactor: Redactor | PlainFormatter, never_send: frozenset[str] = frozenset()) -> str:
    parts: list[str] = []
    if view.name and "name" not in never_send:
        shown = redactor.name_for_prompt(view.name)
        if shown:
            parts.append(f"name: {shown}")
    if view.age is not None and "age" not in never_send:
        parts.append(f"age: {view.age}")
    for label in ("location", "profession", "language"):
        v = getattr(view, label)
        if v and label not in never_send:
            parts.append(f"{label}: {redactor.redact(str(v))}")
    for k, v in view.facts[:SAFE_FACT_KEYS_MAX]:
        if k not in never_send and not k.startswith(("pending_", "merge_")):
            parts.append(f"{k}: {redactor.redact(v)[:60]}")
    s = "; ".join(parts)
    return s[:SUMMARY_MAX_CHARS]


def missing_fields(view: ProfileView, never_send: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Fields still worth collecting. Empty if the user declined consent (we must not ask again)."""
    if view.consent_state == "declined":
        return ()
    have = {"name": bool(view.name), "phone": view.has_phone, "email": view.has_email,
            "location": bool(view.location), "age": view.age is not None, "profession": bool(view.profession)}
    return tuple(f for f in COLLECTABLE if not have[f] and f not in view.pending and f not in never_send)
