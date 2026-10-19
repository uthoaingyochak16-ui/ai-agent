"""Multi-turn chat check against the real pipeline and the real AI providers; nothing is kept.

Every scenario is a list of guest messages sent one after another in the same simulated chat, through Test Chat's
code path; the whole chat is rolled back afterwards. Prints one JSON line per turn with the reply and how it was
produced, for a person to read.

    docker exec -i assistant-backend-1 python -m scripts.chat_eval < scenarios.json > results.jsonl
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.test_chat import TestChatIn, _run
from app.db.session import get_engine
from app.llm.service import LLMService
from app.models import Profile

CONCURRENCY = 4


async def scenario(engine, llm, name: str, turns: list[str], gate: asyncio.Semaphore) -> list[dict]:
    """One simulated guest. The whole chat runs inside one transaction that is rolled back at the end, so the chat's
    state (history, pending questions, profile) carries from turn to turn exactly as in a real chat, and nothing stays.
    The guest is a returning one: the name/phone request and the consent question are already behind them."""
    async with gate, engine.connect() as conn:
        trans = await conn.begin()
        sid, out = f"eval{uuid.uuid4().hex[:10]}", []
        try:
            s = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
            prof = Profile(personal_info_request_status="declined", consent_given=True,
                           consent_asked_at=datetime.now(timezone.utc), consent_at=datetime.now(timezone.utc))
            s.add(prof)
            await s.commit()
            for n, text in enumerate(turns, 1):
                try:
                    res = await _run(s, TestChatIn(message=text, session_id=sid, profile_id=prof.id), llm)
                    await s.commit()
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    out.append({"scenario": name, "turn": n, "user": text, "error": repr(exc)})
                    break
                replies = [m["content"] for m in res["messages"] if m["role"] == "assistant"]
                d = res["debug"]
                out.append({"scenario": name, "turn": n, "user": text, "reply": "\n".join(replies), "path": d.get("path"),
                            "router": d.get("router"), "rewrite": (d.get("question_rewrite") or {}).get("rewritten"),
                            "steps": d.get("steps", [])[-4:], "events": d.get("events"), "paused": res["ai_paused"]})
            await s.close()
        finally:
            await trans.rollback()
        return out


async def main() -> None:
    scenarios: dict[str, list[str]] = json.load(sys.stdin)
    engine = get_engine()
    llm = LLMService(async_sessionmaker(engine, expire_on_commit=False))
    gate = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(scenario(engine, llm, k, v, gate) for k, v in scenarios.items()))
    for rows in results:
        for row in rows:
            sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
