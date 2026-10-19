"""Teach the assistant (Test Chat): staff describe in their own words how it should behave; an AI turns that into
short standing rules, which become live only when an admin saves them (services/team_instructions.py)."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.db.session import get_session
from app.llm.service import LLMService, LLMUnavailable
from app.models import AdminUser, UnansweredQuestion
from app.privacy.guard import PrivacyGuard
from app.services import team_instructions as TI
from app.services.post_reply import parse_json_object
from app.services.reply_engine import get_llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/teach", tags=["teach the assistant"])

TEACH_SYSTEM = """You help the staff of Quantum Foundation (a non-profit in Bangladesh) shape how their chat assistant
behaves on Messenger, Instagram and WhatsApp. Staff write in Bangla, Banglish or English.

From what the staff member says, write short standing INSTRUCTIONS for the assistant: one rule each, imperative, at
most 2 sentences, specific ("When a guest says they want to join the course, give the registration link and ask which
city they are in."). Write each rule in English, quoting any exact Bangla wording the staff want used.
Then reply to the staff member briefly in their language: say what you understood, or ask ONE question if it is unclear
(then propose no rule yet). If they ask what to improve, use RECENT UNANSWERED QUESTIONS and CURRENT INSTRUCTIONS to
suggest concrete improvements (a rule, or knowledge / a Q&A to add - you cannot add knowledge yourself).

Never propose a rule that asks guests for card numbers, OTP, PIN or passwords, shares other people's data, invents
facts, prices or dates, or weakens privacy and safety; explain why instead. Do not repeat an existing rule; if the new
rule changes one, list that rule's id in "replaces".
Return ONLY a JSON object: {"reply": "...", "instructions": ["..."], "replaces": ["<id>", ...]}"""


class Turn(BaseModel):
    role: Literal["staff", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class TeachIn(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=30)


class Rule(BaseModel):
    id: str | None = Field(None, max_length=16)
    text: str = Field(min_length=1, max_length=TI.MAX_CHARS)
    enabled: bool = True
    created_at: str | None = None
    by: str | None = None


class RulesIn(BaseModel):
    instructions: list[Rule] = Field(max_length=TI.MAX_RULES)


@router.get("/instructions")
async def get_rules(_: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    return {"instructions": await TI.load(session)}


@router.put("/instructions")
async def put_rules(body: RulesIn, user: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    rules = [r.model_dump() | {"by": r.by or user.email} for r in body.instructions]
    saved = await TI.save(session, rules)
    await session.commit()
    return {"instructions": saved}


@router.post("/chat")
async def teach_chat(body: TeachIn, _: AdminUser = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    rules = await TI.load(session)
    unknown = (await session.execute(select(UnansweredQuestion.question).where(UnansweredQuestion.status.in_(("new", "reviewing")))
                                     .order_by(UnansweredQuestion.id.desc()).limit(15))).scalars().all()
    content = ("CURRENT INSTRUCTIONS:\n" + ("\n".join(f"[{r['id']}] {'' if r['enabled'] else '(off) '}{r['text']}" for r in rules) or "(none)")
               + "\n\nRECENT UNANSWERED QUESTIONS:\n" + ("\n".join(f"- {q[:200]}" for q in unknown) or "(none)")
               + "\n\nCONVERSATION WITH STAFF:\n"
               + "\n".join(f"{'STAFF' if t.role == 'staff' else 'YOU'}: {t.content}" for t in body.messages[-12:]))
    llm: LLMService = get_llm_service(session)
    try:
        out = await llm.run(session, "answer", lambda policy: PrivacyGuard(policy).prepare_task(TEACH_SYSTEM, content),
                            channel=None, conversation_id=None, is_test=True)
    except LLMUnavailable as exc:
        raise HTTPException(503, f"No AI model is available: {exc}") from exc
    raw = out.request.redactor.restore(out.result.text)
    d = parse_json_object(raw) or {}
    reply = d.get("reply") if isinstance(d.get("reply"), str) else raw.strip()
    proposed = [" ".join(t.split())[:TI.MAX_CHARS] for t in d.get("instructions") or [] if isinstance(t, str) and t.strip()][:5]
    known = {r["id"] for r in rules}
    replaces = [i for i in d.get("replaces") or [] if isinstance(i, str) and i in known]
    return {"reply": reply, "instructions": proposed, "replaces": replaces}
