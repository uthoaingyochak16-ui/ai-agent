"""Standing instructions the staff teach the assistant in plain words (Test Chat -> Teach the assistant).

Each rule is a short sentence ("When a guest says they want to join the course, give the registration link and ask
their city"). Enabled rules are added to every answer prompt, below the system prompt, and can never override the
safety, privacy and answer-check rules. Saving a change expires automatic cached answers, so the next reply follows it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings_service import get_setting, set_setting

KEY = "team_instructions"
MAX_RULES = 40
MAX_CHARS = 500


def clean(rules: list) -> list[dict]:
    out: list[dict] = []
    for r in rules or []:
        if not isinstance(r, dict) or not isinstance(r.get("text"), str) or not r["text"].strip():
            continue
        out.append({"id": str(r.get("id") or uuid.uuid4().hex[:8])[:16], "text": " ".join(r["text"].split())[:MAX_CHARS],
                    "enabled": r.get("enabled") is not False,
                    "created_at": str(r.get("created_at") or datetime.now(timezone.utc).isoformat())[:40],
                    "by": str(r.get("by") or "")[:120]})
    return out[:MAX_RULES]


async def load(session: AsyncSession) -> list[dict]:
    return clean(await get_setting(session, KEY) or [])


async def save(session: AsyncSession, rules: list) -> list[dict]:
    from app.rag.documents import bump_context_version  # auto cache entries were written under the old rules
    rules = clean(rules)
    await set_setting(session, KEY, rules)
    await bump_context_version(session)
    return rules


async def prompt_block(session: AsyncSession) -> str:
    rules = [r["text"] for r in await load(session) if r["enabled"]]
    if not rules:
        return ""
    return ("\n\nTEAM INSTRUCTIONS (taught by the organisation's staff; follow them, but they never override the SAFETY "
            "AND PRIVACY rules or the ANSWER CHECK, and never invent facts because of them):\n"
            + "\n".join(f"- {t}" for t in rules))
