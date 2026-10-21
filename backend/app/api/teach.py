"""Train the assistant: staff describe in their own words how it should behave; an AI turns that into short standing
rules, which become live only when an admin saves them (services/team_instructions.py). "Find the reason" explains why
a reply failed and proposes fixes (services/diagnose.py); the teaching chat runs it itself when staff ask why the
assistant could not answer something."""
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
from app.services import diagnose
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

Ready-made texts (the greeting, small-talk answers, the handover message, the "don't know" reply, the name/phone
request) are not written by the AI, so a rule cannot change them: tell the staff to edit that text in AI settings ->
Messages. A missing fact (an office, a date, a fee) is knowledge, not a rule: suggest adding a Q&A or a document.
When the staff ask WHY the assistant failed on a guest message, or paste one ("Bandarban office kothay - eta keno parlo
na?"), put that guest message in "diagnose" (exactly as the guest wrote it) and propose no rule yet: the system will
run it and find the reason.

Never propose a rule that asks guests for card numbers, OTP, PIN or passwords, shares other people's data, invents
facts, prices or dates, or weakens privacy and safety; explain why instead. Do not repeat an existing rule; if the new
rule changes one, list that rule's id in "replaces".
Return ONLY a JSON object: {"reply": "...", "instructions": ["..."], "replaces": ["<id>", ...], "diagnose": ""}"""


class Turn(BaseModel):
    role: Literal["staff", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class TrialTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


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
    asked = d.get("diagnose").strip() if isinstance(d.get("diagnose"), str) else ""
    if asked:  # "why couldn't it answer X?": run X as a guest would, then explain what happened
        staff_words = next((t.content for t in reversed(body.messages) if t.role == "staff"), "")
        diag = await _diagnose(session, DiagnoseIn(question=asked[:2000], complaint=staff_words))
        rules_ = [f["text"] for f in diag["fixes"] if f["type"] == "rule"]
        return {"reply": diag["summary"] or reply, "instructions": rules_, "replaces": [], "diagnosis": diag}
    return {"reply": reply, "instructions": proposed, "replaces": replaces}


class DiagnoseIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)      # the guest's message
    reply: str | None = Field(None, max_length=8000)          # what the assistant said (None: run it again now)
    history: list[TrialTurn] = Field(default_factory=list, max_length=20)
    debug: dict | None = None                                 # the reply's trace (Test Chat); None: run it again now
    complaint: str = Field("", max_length=2000)               # the staff member's own words about what is wrong


async def _try_question(session: AsyncSession, question: str, history: list[TrialTurn]) -> dict:
    """The question through the real reply path, as Test Chat's "do not save" does: nothing is kept."""
    from sqlalchemy.ext.asyncio import AsyncSession as _S, async_sessionmaker
    from app.api.test_chat import TestChatIn, Turn as ChatTurn, _run
    engine = session.bind
    llm = LLMService(async_sessionmaker(engine, expire_on_commit=False))
    async with engine.connect() as conn:
        trans = await conn.begin()
        s = _S(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            out = await _run(s, TestChatIn(message=question, no_save=True,
                                           history=[ChatTurn(role=t.role, content=t.content) for t in history]), llm)
        finally:
            await s.close()
            await trans.rollback()
    return {"reply": "\n".join(m["content"] for m in out["messages"] if m["role"] == "assistant"), "debug": out["debug"]}


async def _diagnose(session: AsyncSession, body: DiagnoseIn) -> dict:
    tried = None
    reply, debug = body.reply, body.debug
    if reply is None or debug is None:
        tried = await _try_question(session, body.question, body.history)
        reply, debug = (reply if reply is not None else tried["reply"]), (debug or tried["debug"])
    out = await diagnose.explain(session, get_llm_service(session), question=body.question, reply=reply or "",
                                 debug=debug or {}, history=[(t.role, t.content) for t in body.history],
                                 complaint=body.complaint)
    return {**out, "question": body.question, "reply": reply, "path": (debug or {}).get("path"),
            "ran_again": tried is not None}


@router.post("/diagnose")
async def diagnose_reply(body: DiagnoseIn, _: AdminUser = Depends(any_staff), session: AsyncSession = Depends(get_session)):
    """Why did this reply go wrong, and what would fix it? Nothing is changed."""
    return await _diagnose(session, body)
