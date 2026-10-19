"""Reply inspector: one plain summary of how a reply was made (Test chat, and stored on every customer reply so the
Inbox can show it), plus `why`: short plain-language reasons, for "Why did this happen?" on a wrong or empty reply.

Built from the pipeline's debug dict only; it never adds what the admin cannot already see (no prompt, no PII beyond
the profile summary staff see on the profile page).
"""
from __future__ import annotations

from app.services.language import style

# reply path -> what an admin calls it
PATH_GROUP = {"qa_shortcut": "Q&A", "variable": "Q&A (variable)", "cache": "Q&A (saved AI answer)", "program": "program",
              "llm": "general AI", "llm_large": "general AI", "kb_extract": "knowledge text (no AI)",
              "tool": "tool", "tool_confirm": "tool", "handover": "handover", "router_handover": "handover",
              "no_context": "unknown → handover offer", "llm_unavailable": "unknown (AI unavailable)",
              "payment_disabled": "payment → human handover",
              "greeting": "canned greeting", "smalltalk": "canned small talk", "lead": "profile request (name/phone)",
              "data_question": "canned (data question)", "district": "program follow-up (district)"}


def build(*, question: str, debug: dict, reply: str, qtype: str, profile_facts: list[str] | None = None,
          followups: list[str] | None = None, paused: bool = False) -> dict:
    d = debug or {}
    path = d.get("path") or ("handover" if paused else "none")
    lang = style(question) or "unknown (too short)"
    matched: list[str] = []
    if d.get("qa_match"):
        q = d["qa_match"]
        matched.append(f"Q&A #{q['id']} \"{q['question']}\" ({'exact' if q.get('exact') else 'similarity ' + str(q.get('similarity'))}"
                       + (f", {q['category']}" if q.get("category") else "") + ")")
    if d.get("variable_match"):
        matched.append(f"Variable {{{d['variable_match']['key']}}}")
    if d.get("program_match"):
        names = d["program_match"].get("names") or [str(i) for i in d["program_match"].get("ids", [])]
        matched.append("Program: " + ", ".join(f'"{n}"' for n in names))
    if d.get("cache"):
        matched.append(f"Saved answer to \"{d['cache'].get('cached_question')}\"")
    ks = d.get("keyword_search") or {}
    context = [f"{c.get('document')}: {c.get('preview', '')[:120]}" for c in (d.get("chunks") or [])[:4]]
    model = None
    if d.get("model"):
        model = f"{d.get('provider')} / {d.get('model')}"
    elif d.get("router"):
        model = "router only"
    notes = []
    if d.get("fallback_used"):
        notes.append("fallback provider used")
    if d.get("budget_fallback"):
        notes.append("budget fallback model used")
    if d.get("error"):
        notes.append(str(d["error"]))
    out = {"question": question, "language": lang, "question_type": qtype, "path": path,
           "path_group": PATH_GROUP.get(path, path), "profile_facts": profile_facts or [], "matched": matched,
           "keywords": list(ks.get("keywords") or [])[:12], "context": context, "model": model,
           "errors": notes, "final_reply": reply, "followups": followups or []}
    out["why"] = why(out, d, paused)
    return out


def why(i: dict, d: dict, paused: bool = False) -> list[str]:
    """Plain-language reasons, most important first."""
    out: list[str] = []
    path, steps = i["path"], " ".join(d.get("steps") or [])
    if not (i["final_reply"] or "").strip():
        out.append("No reply was sent: the AI is paused because a team member has taken over this chat."
                   if paused else "No reply was produced for this message.")
    if path == "no_context":
        if "organisation-specific" in steps:
            out.append("The question is about the organisation, but nothing in Q&A, Variables, Programs or the knowledge "
                       "base matched it. The AI was not asked, so it could not make up an address, fee or date. "
                       "Add a Q&A or knowledge for it (it is now in Q&A manager → Unknown questions).")
        else:
            out.append("Nothing in Q&A, Variables, Programs or the knowledge base matched, and general AI answers are "
                       "switched off (AI settings), so the 'don't know' reply was sent.")
    if path == "llm_unavailable" or "llm_unavailable" in (d.get("error") or "") or (d.get("error") and not d.get("model")):
        out.append(f"The AI model could not be used ({d.get('error') or 'no route'}). Check AI settings → Model routing "
                   "and the provider key / budget.")
    if "unresolved variables" in steps:
        out.append("A Q&A matched, but it uses a Variable that has no value yet, so it was skipped. Give the variable a "
                   "value in Q&A manager → Variables.")
    if d.get("output_blocked"):
        out.append(f"The AI's reply was blocked by the privacy filter ({', '.join(d.get('output_reasons') or [])}) and "
                   "replaced with a safe message.")
    if d.get("fallback_used"):
        out.append("The main AI provider failed, so the fallback provider answered.")
    if path in ("llm", "llm_large") and not d.get("chunks") and not d.get("keyword_search", {}).get("used"):
        out.append("No knowledge matched; this is a general AI answer (general answers are on).")
    ks = d.get("keyword_search") or {}
    if path in ("llm", "llm_large", "no_context") and ks and not ks.get("used"):
        out.append("Keyword search found no record covering enough of the question"
                   + (f" ({ks.get('reason')})" if ks.get("reason") else "") + ".")
    cands = d.get("qa_candidates") or []
    if path not in ("qa_shortcut", "variable") and cands and cands[0].get("similarity", 0) >= 0.8:
        out.append(f"The closest Q&A (\"{cands[0]['question']}\", similarity {cands[0]['similarity']}) was below the match "
                   "threshold; add this wording as a Q&A variant if it means the same.")
    if path == "lead":
        out.append("This was the one-time request for the guest's name and phone; their question is answered after they reply.")
    if path in ("handover", "router_handover"):
        out.append("The guest asked for a person (or the message needs one), so the chat was handed to the team.")
    if path == "tool_confirm":
        out.append("The assistant wants to take an action (lead / callback) and asked the guest to confirm first.")
    if not out:
        out.append(f"Answered by: {i['path_group']}. Nothing unusual happened.")
    return out
