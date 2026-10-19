"""Golden test set: run every chat in scripts/golden_set.json through the real reply path (as Test Chat does, nothing
is kept) and let Claude judge each reply against what it must do. Prints a score and writes a JSON report.

    docker exec -i assistant-backend-1 python -m scripts.golden_eval < scripts/golden_set.json > golden_report.json

The judge gets today's date, the upcoming programs and the saved values, so program answers are judged against the
schedule of the day. A turn marked "handover" passes only if the chat was handed to a person.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.db.session import get_engine, get_sessionmaker
from app.llm.providers import AnthropicProvider
from app.llm.service import LLMService
from app.models import LLMProviderConfig, Variable
from app.privacy.guard import PrivacyGuard, ProviderPolicy
from app.rag.programs import context_line, today_bd, upcoming
from app.services.post_reply import parse_json_object
from scripts.chat_eval import CONCURRENCY, scenario

JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_SYSTEM = """You grade one reply of a Bangladeshi organisation's chat assistant. You get REFERENCE facts (today's
date, the organisation's upcoming programs, saved values), the CHAT so far, the assistant's REPLY to the last guest
message, and EXPECTED: what that reply must do. Judge against EXPECTED and REFERENCE. The assistant also has a
knowledge base you cannot see, so a specific extra detail that is not in REFERENCE is not a failure by itself; fail
only details that contradict EXPECTED or REFERENCE, or are clearly made up. Marks like [ADDRESS_1] or [PHONE_1] are
masking in the stored copy of the reply (the guest saw the real text): treat them as the real value being present.
Bangla and English numbers are equal (১১,৫০০ = 11,500). Return ONLY JSON: {"pass": true|false, "reason": "one short
sentence"}"""


async def _judge_client() -> AnthropicProvider:
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == "anthropic"))).scalar_one()
        return AnthropicProvider(crypto.decrypt(cfg.api_key_enc, aad="llm:anthropic"), timeout_s=90, retries=2)


async def _reference() -> str:
    async with get_sessionmaker()() as s:
        today = today_bd()
        programs = "\n".join(context_line(p, today) for p in await upcoming(s, today))
        values = "\n".join(f"{v.key}: {v.value}" for v in (await s.execute(select(Variable))).scalars() if (v.value or "").strip())
    return f"Today: {today.isoformat()}\nUPCOMING PROGRAMS:\n{programs or '(none)'}\nSAVED VALUES:\n{values or '(none)'}"


async def judge(client: AnthropicProvider, reference: str, chat: list[str], turn: dict, row: dict) -> dict:
    if turn.get("handover") and not row.get("paused"):
        return {"pass": False, "reason": "expected a handover to a person, the chat was not handed over"}
    content = (f"REFERENCE:\n{reference}\n\nCHAT:\n" + "\n".join(chat) +
               f"\n\nREPLY:\n{row.get('reply') or '(no reply)'}\n\nEXPECTED:\n{turn['expect']}")
    req = PrivacyGuard(ProviderPolicy(name="judge", allow_pii=True, name_mode="none")).prepare_task(JUDGE_SYSTEM, content)
    try:
        out = await client.generate(req, model=JUDGE_MODEL, max_tokens=300, temperature=None)
        verdict = parse_json_object(out.text) or {}
        return {"pass": bool(verdict.get("pass")), "reason": str(verdict.get("reason", ""))[:300]}
    except Exception as exc:  # noqa: BLE001 - a judge failure is reported, not a crash
        return {"pass": False, "reason": f"judge failed: {exc}"[:300]}


async def main() -> None:
    cases = json.load(sys.stdin)["cases"]
    engine = get_engine()
    llm = LLMService(async_sessionmaker(engine, expire_on_commit=False))
    gate = asyncio.Semaphore(CONCURRENCY)
    runs = await asyncio.gather(*(scenario(engine, llm, c["id"], [t["user"] for t in c["turns"]], gate) for c in cases))
    client, reference = await _judge_client(), await _reference()

    results = []
    for case, rows in zip(cases, runs):
        chat: list[str] = []
        for turn, row in zip(case["turns"], rows):
            chat.append(f"GUEST: {turn['user']}")
            verdict = {"pass": False, "reason": row["error"]} if "error" in row else await judge(client, reference, chat, turn, row)
            chat.append(f"ASSISTANT: {row.get('reply', '')}")
            results.append({"case": case["id"], "tags": case.get("tags", []), "turn": row["turn"], "user": turn["user"],
                            "expect": turn["expect"], "reply": row.get("reply"), "path": row.get("path"),
                            "paused": row.get("paused"), **verdict})
        for turn in case["turns"][len(rows):]:  # the chat stopped early (error): the remaining turns fail
            results.append({"case": case["id"], "tags": case.get("tags", []), "user": turn["user"], "expect": turn["expect"],
                            "reply": None, "pass": False, "reason": "not reached"})

    passed = sum(r["pass"] for r in results)
    by_tag: dict[str, list[int]] = {}
    for r in results:
        for t in r["tags"]:
            by_tag.setdefault(t, [0, 0])[0] += r["pass"]
            by_tag[t][1] += 1
    summary = {"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "passed": passed, "total": len(results),
               "score": round(100 * passed / max(1, len(results)), 1),
               "by_tag": {t: f"{p}/{n}" for t, (p, n) in sorted(by_tag.items())}}
    print(f"golden set: {passed}/{len(results)} = {summary['score']}%", file=sys.stderr)
    json.dump({"summary": summary, "results": results}, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
