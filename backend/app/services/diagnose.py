"""Why did the assistant fail? (Train the assistant -> "Find the reason")

1. signals(debug): what the reply trace says for certain, in plain words (no AI): which step produced the reply
   (saved Q&A, cache, a ready-made message, the AI), what the knowledge search found, a safety block, a policy, an
   outage. These are facts, so the explanation never guesses at them.
2. explain(): one AI call reads the guest's question, the reply, those facts, the retrieved knowledge, the team rules
   and the staff member's own words ("eta bhul, fee bolar dorkar chilo na") and returns, in the staff member's language,
   what went wrong and the fixes that would work: a team rule, a saved Q&A, knowledge to add, or a message to edit.
   Nothing is changed here; staff save the fix they want.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.service import LLMService, LLMUnavailable
from app.privacy.guard import PrivacyGuard
from app.services import team_instructions as TI
from app.services.post_reply import parse_json_object

CAUSES = ("missing_knowledge", "knowledge_not_matched", "misunderstood", "rule_or_style", "saved_answer_wrong",
          "ready_message", "safety_filter", "policy", "technical", "other")
FIX_TYPES = ("rule", "qa", "knowledge", "message")
# replies that come from a ready-made text, not from the AI: a team rule cannot change them, the text itself must be edited
CANNED = {"greeting": "the greeting message", "smalltalk": "a small-talk answer", "handover": "the handover message",
          "router_handover": "the handover message", "lead": "the name/phone request", "data_question": "the data-saving answer",
          "district": "the nearby-program message", "router_erasure": "the delete-my-data message",
          "router_consent": "the consent message", "followup": "a follow-up bubble"}


def signals(d: dict) -> list[str]:
    """Facts from a reply's debug trace, most telling first."""
    d = d or {}
    path, out = d.get("path") or "none", []
    steps = " | ".join(d.get("steps") or [])
    if path in CANNED:
        out.append(f"The reply is {CANNED[path]} (a ready-made text, no AI). A team rule cannot change it; edit that "
                   "text in AI settings -> Messages.")
    if path == "qa_shortcut":
        m = d.get("qa_match") or {}
        out.append(f"A saved Q&A answered it without the AI: #{m.get('id')} \"{m.get('question')}\" "
                   f"(similarity {m.get('similarity')}). If that answer is wrong or the question differs, fix that Q&A.")
    if path == "cache":
        c = d.get("cache") or {}
        out.append(f"An earlier AI answer was reused from the cache (to \"{c.get('cached_question')}\").")
    if path == "variable":
        out.append(f"A saved value (Variables) answered it: {(d.get('variable_match') or {}).get('key')}.")
    if path == "payment_disabled":
        out.append("Payment questions (pay, bKash, refund, receipt ...) are never answered by the AI: a person handles them.")
    if path in ("llm_unavailable",) or d.get("error"):
        out.append(f"No AI model could answer: {d.get('error') or 'provider error'}.")
    if "embedding unavailable" in steps:
        out.append("The meaning search was down (embedding quota/outage): only word search ran, so knowledge was easy to miss.")
    chunks = d.get("chunks")
    if path == "no_context" and not chunks:
        out.append("The knowledge search found nothing related: the knowledge base probably does not have this.")
    elif path == "no_context":
        out.append(f"The search found {len(chunks)} candidate(s) but the AI judged none of them answers the question "
                   "(the right detail is missing, or the wording does not match).")
    if d.get("output_blocked"):
        out.append(f"The safety filter blocked the AI's answer ({', '.join(d.get('output_reasons') or [])}) and a safe "
                   "reply was sent instead.")
    if path == "safety_fallback":
        out.append("The AI's answer was replaced by the 'don't know' reply because of the safety filter.")
    r = d.get("router") or {}
    if r:
        out.append(f"The AI read the message as: intent {r.get('intent')}"
                   + (f", wants: {r.get('request')}" if r.get("request") else "")
                   + (", needs a person" if r.get("needs_human") else "") + ".")
    rw = d.get("question_rewrite") or {}
    if rw.get("used"):
        out.append(f"The question was searched as: \"{rw.get('rewritten')}\".")
    if d.get("tools"):
        out.append("Tools were called: " + ", ".join(c for t in d["tools"] for c in t.get("calls", [])) + ".")
    if path in ("llm", "llm_large") and not out:
        out.append("The AI wrote this answer from the retrieved knowledge.")
    return out


DIAGNOSE_SYSTEM = """You help the staff of Quantum Foundation (a non-profit in Bangladesh) understand why their chat
assistant gave a wrong or unhelpful reply, and how to fix it. Staff write casually, in Bangla, Banglish or English.

You get the guest's QUESTION (and the chat before it), the assistant's REPLY, FACTS (certain: how the reply was made),
the KNOWLEDGE the search found (previews), the TEAM RULES in force, and what the STAFF said is wrong (may be empty).
Work out the real cause. Trust FACTS over guesses. Typical causes:
missing_knowledge (the knowledge base does not have it), knowledge_not_matched (it is there but was not found or used),
misunderstood (the question was read wrongly), rule_or_style (the answer is right but should be said differently, or
a team rule is missing / wrong), saved_answer_wrong (a saved Q&A or cached answer is wrong), ready_message (a
ready-made text was sent), safety_filter, policy (e.g. payments go to a person), technical (AI or search down), other.

Then propose the fixes that would really solve it, most useful first (1 to 3):
- {"type": "rule", "text": "..."}: a standing instruction in English, imperative, at most 2 sentences, specific. Only
  when behaviour must change; never for facts the knowledge base lacks; never one that would affect unrelated questions.
- {"type": "qa", "question": "...", "answer": "..."}: a saved question and answer, when the correct answer is a fact
  the staff gave or that FACTS / KNOWLEDGE show; never invent facts, prices, dates or phone numbers. Answer in the
  guest's language.
- {"type": "knowledge", "note": "..."}: what document or detail to add, when the fact is unknown to you.
- {"type": "message", "note": "..."}: which ready-made text to edit (AI settings -> Messages) and how.
Never propose anything that asks guests for card numbers, OTP, PIN or passwords, or weakens privacy or safety.
Return ONLY JSON: {"summary": "2-3 plain sentences in the STAFF's language (Bangla script if they wrote Bangla or
Banglish; Bangla if they wrote nothing) saying what went wrong and why", "cause": "<one cause>", "fixes": [...]}"""


async def explain(session: AsyncSession, llm: LLMService, *, question: str, reply: str, debug: dict,
                  history: list[tuple[str, str]] = (), complaint: str = "") -> dict:
    facts = signals(debug)
    chunks = (debug or {}).get("chunks") or []
    rules = [r["text"] for r in await TI.load(session) if r["enabled"]]
    content = ("CHAT BEFORE:\n" + ("\n".join(f"{'GUEST' if r == 'user' else 'ASSISTANT'}: {t[:400]}" for r, t in history[-6:]) or "(none)")
               + f"\n\nQUESTION: {question[:1500]}\n\nREPLY: {reply[:2000] or '(no reply)'}"
               + "\n\nFACTS:\n" + ("\n".join(f"- {f}" for f in facts) or "(none)")
               + "\n\nKNOWLEDGE FOUND:\n" + ("\n".join(f"- [{c.get('document')}] {c.get('preview', '')[:200]}" for c in chunks[:6]) or "(nothing)")
               + "\n\nTEAM RULES:\n" + ("\n".join(f"- {t}" for t in rules) or "(none)")
               + f"\n\nSTAFF SAID: {complaint[:1500] or '(nothing)'}")
    try:
        out = await llm.run(session, "answer", lambda policy: PrivacyGuard(policy).prepare_task(DIAGNOSE_SYSTEM, content),
                            channel=None, conversation_id=None, is_test=True)
    except LLMUnavailable as exc:
        return {"facts": facts, "summary": None, "cause": None, "fixes": [], "error": f"No AI model is available: {exc}"}
    raw = out.request.redactor.restore(out.result.text)
    d = parse_json_object(raw) or {}
    fixes = []
    for f in d.get("fixes") or []:
        if not isinstance(f, dict) or f.get("type") not in FIX_TYPES:
            continue
        keep = {k: " ".join(str(f.get(k) or "").split())[:TI.MAX_CHARS if k == "text" else 2000]
                for k in ("text", "question", "answer", "note")}
        if any(keep.values()):
            fixes.append({"type": f["type"], **{k: v for k, v in keep.items() if v}})
    return {"facts": facts, "summary": d.get("summary") if isinstance(d.get("summary"), str) else raw.strip()[:1500],
            "cause": d.get("cause") if d.get("cause") in CAUSES else "other", "fixes": fixes[:3]}
