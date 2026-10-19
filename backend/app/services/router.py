"""LLM intent router: one cheap JSON call that understands what the word lists miss.

It runs only after the free deterministic checks (exact erasure/handover keyword, greeting and small-talk lists,
wording-level Q&A, variables) found nothing, and only when `router_enabled` is on and a "router" route exists.
Its output maps onto the existing actions in services/ingest.py; any failure means "no decision" and today's
rule-based flow continues. The request is built through PrivacyGuard like every other LLM call.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.privacy.guard import PrivacyGuard
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

INTENTS = ("greeting", "smalltalk", "question", "handover_request", "consent_yes", "consent_no", "erasure_request",
           "complaint", "other")
LANGUAGES = ("bn", "en", "banglish")
HISTORY_TURNS = 4

ROUTER_SYSTEM = """You read the LAST customer message in a chat with a Bangladeshi organisation's assistant and work out
exactly what the customer wants, before anyone answers. Customers write Bangla, English or Banglish (Bangla in Latin
letters), with spelling mistakes, in short fragments, or as a follow-up to earlier turns. Read for the MEANING, not the
words, and use the RECENT TURNS to see what a short or vague message refers to. Return ONLY a JSON object:
{"intent": "greeting"|"smalltalk"|"question"|"handover_request"|"consent_yes"|"consent_no"|"erasure_request"|"complaint"|"other",
 "language": "bn"|"en"|"banglish", "needs_human": true|false, "standalone_question": string|null, "request": string|null}
Rules:
- greeting: only a hello / salam with no request. smalltalk: chit-chat about the assistant (who are you, how are you, thanks, bye).
- question: asks for information or help. Put a self-contained version in standalone_question, resolving references
  to earlier turns ("oita koto?" -> what "oita" refers to). It is used to search a knowledge base written in Bangla
  script: write it in Bangla script when the customer writes Bangla or Banglish ("sadakayon ki?" -> "সাদাকায়ন কী?",
  "Dhakai next course kobe?" -> "ঢাকায় পরবর্তী কোর্স কবে?"), in English only when the customer writes English.
  Otherwise null.
- handover_request: wants a human / staff / phone call. complaint: unhappy, reports a problem with a service or staff.
  An urgent need (blood, a medical emergency) is not a complaint: it is a question, with needs_human true.
- consent_yes / consent_no: ONLY when CONSENT_ASKED is yes and the message answers that question.
- A bare acknowledgement ("ji", "ok", "accha", "thik ache", "thanks") is smalltalk, unless the ASSISTANT's last turn
  asked the customer something: then it is that question's answer, intent question, and standalone_question says what
  the customer agreed to (e.g. "Yes, I want to join the next course in Dhaka").
- Follow-ups: a short message ("kobe?", "kothay?", "fee?", "oita ki?", "Dhakai?", "ar Chottogram e?") continues the
  topic of the RECENT TURNS. Name that topic (the program, office or service last discussed) in standalone_question and
  request; never leave it vague.
- erasure_request: asks to delete / forget their personal data.
- request: for a question, ONE short English line naming exactly what the customer wants: the subject (which
  program, service, office or topic) and the specific detail asked (date, fee, place, time, how to, eligibility,
  duration, contact ...), plus any condition they gave (their city, a student, for a child ...). E.g. "Date of the next
  Quantum Method course in Dhaka", "Whether a 15-year-old can join the meditation course". Otherwise null.
- needs_human: true for handover_request, complaints, emergencies, or anything the assistant must not handle alone.
  ALWAYS true when the customer wants to pay, asks how or where to pay (bKash, Nagad, bank ...), says they paid or
  money was deducted, or asks about a refund, a receipt, instalments or a payment problem: payments are handled only
  by staff. Asking how much something costs (the fee or price) is a normal question: needs_human false.
- language: "en" only when the customer writes English sentences, or the chat so far is in English. A single word or
  short fragment ("fee?", "Dhakai?", "ok", "link?") in a Bangla/Banglish chat, or with no earlier turns, is "banglish".
- Tokens like [PHONE_1] are privacy placeholders; keep them unchanged. Never answer the question yourself."""


@dataclass
class RouterDecision:
    intent: str
    language: str | None = None
    needs_human: bool = False
    standalone_question: str | None = None
    request: str | None = None             # what exactly the guest wants, for the answer step

    def as_debug(self) -> dict:
        return asdict(self)


def parse_decision(text: str) -> RouterDecision | None:
    """Strict schema check: anything unexpected is treated as no decision."""
    d = parse_json_object(text or "")
    if not d or d.get("intent") not in INTENTS:
        return None
    lang = d.get("language") if d.get("language") in LANGUAGES else None
    sq = d.get("standalone_question")
    sq = sq.strip()[:1000] if isinstance(sq, str) and sq.strip() else None
    req = d.get("request")
    req = req.strip()[:300] if isinstance(req, str) and req.strip() else None
    return RouterDecision(intent=d["intent"], language=lang, needs_human=d.get("needs_human") is True,
                          standalone_question=sq, request=req if d["intent"] == "question" else None)


async def route_intent(session: AsyncSession, llm: LLMService, *, text: str, history: list[tuple[str, str]],
                       consent_asked: bool, channel: str | None, conversation_id: int | None,
                       is_test: bool = False) -> RouterDecision | None:
    """None = router off, not configured, failed or unparseable: the caller keeps today's rules."""
    if not text.strip() or not await get_setting(session, "router_enabled"):
        return None
    turns = "\n".join(f"{'CUSTOMER' if role == 'user' else 'ASSISTANT'}: {t}" for role, t in history[-HISTORY_TURNS:])
    content = (f"CONSENT_ASKED: {'yes' if consent_asked else 'no'}\n"
               f"RECENT TURNS:\n{turns or '(none)'}\n"
               f"LAST CUSTOMER MESSAGE:\n{text}")

    def build(policy):
        g = PrivacyGuard(policy)
        return g.prepare_task(ROUTER_SYSTEM, content)
    try:
        out = await llm.run(session, "router", build, channel=channel, conversation_id=conversation_id, is_test=is_test)
    except LLMUnavailable as exc:
        logger.info("intent router unavailable: %s", exc)
        return None
    except Exception:  # noqa: BLE001 - routing is an optimisation; the reply must never depend on it
        logger.exception("intent router failed")
        return None
    decision = parse_decision(out.result.text)  # placeholders stay placeholders: retrieval doesn't need the values
    if decision is None:
        logger.info("intent router returned an unusable answer")
    return decision
