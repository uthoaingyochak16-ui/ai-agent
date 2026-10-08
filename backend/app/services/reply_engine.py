from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm import service as llm_service
from app.models import Message
from app.services import consent as consent_svc
from app.services.language import localized
from app.services.pipeline import run_pipeline


@dataclass
class ReplyResult:
    text: str
    path: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    sources: dict | None = None
    debug: dict | None = None


def get_llm_service(session: AsyncSession) -> llm_service.LLMService:
    return llm_service.LLMService(async_sessionmaker(session.bind, expire_on_commit=False))


def compact_sources(res) -> dict:
    d = res.debug
    out = {"path": res.path}
    if "qa_match" in d:
        out["qa_id"] = d["qa_match"]["id"]
    if d.get("chunks"):
        out["chunks"] = [{"document": c["document"], "page": c["page"], "similarity": c["similarity"]} for c in d["chunks"]]
    for k in ("model", "provider", "cost_usd"):
        if k in d:
            out[k] = d[k]
    return out


async def generate_reply(session: AsyncSession, resolved, text: str, llm=None, *, knowledge_only: bool = False) -> ReplyResult:
    res = await run_pipeline(session, llm or get_llm_service(session), text=text, profile=resolved.profile,
                             conversation=resolved.conversation, channel=resolved.identity.channel,
                             is_test=resolved.identity.external_id.startswith("test:"), knowledge_only=knowledge_only)
    reply = res.text
    # ask for consent exactly once, from the 2nd user message on, after their question has been answered
    p = resolved.profile
    greeting = (res.debug.get("qa_match") or {}).get("category") == "greeting"   # "Hi"/"Thanks" are not a handled request
    if not knowledge_only and consent_svc.consent_state(p) == "unknown" and res.path not in ("no_context", "llm_unavailable") and not greeting:
        n_user = (await session.execute(select(func.count()).select_from(Message).where(
            Message.conversation_id == resolved.conversation.id, Message.role == "user"))).scalar_one()
        if n_user >= 2:
            reply = f"{reply}\n\n{await localized(session, 'consent_prompt', text)}"
            consent_svc.mark_consent_asked(p)
    return ReplyResult(reply, res.path, res.tokens_in, res.tokens_out, compact_sources(res), res.debug)
