from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import crypto
from app.llm.providers import LLMError
from app.llm.service import LLMService, cost_of
from app.models import (EvalCase, EvalResult, EvalRun, LLMModel, LLMProviderConfig, LLMRoute)
from app.privacy.guard import DEFAULT_SYSTEM_PROMPT, PrivacyGuard, ProviderPolicy
from app.services.pipeline import run_pipeline
from app.services.settings_service import get_setting

SNAPSHOT_KEYS = ("qa_threshold", "qa_text_threshold", "cache_threshold", "retrieval_min_similarity",
                 "escalate_below", "top_k", "chunk_token_budget", "embedding_provider", "embedding_model",
                 "rerank_provider", "rerank_model", "rerank_candidates", "rerank_min_score")
JUDGE_SYSTEM = """You evaluate customer-support answers. Return ONLY JSON with this exact shape:
{"correct": true_or_false, "grounded": true_or_false, "reason": "short explanation"}
Correct means the actual answer conveys the expected answer. Grounded means it is supported by the retrieved context or is a deterministic Q&A answer. Do not reward plausible unsupported claims."""


async def settings_snapshot(session: AsyncSession) -> dict:
    prompt = (await get_setting(session, "system_prompt")) or DEFAULT_SYSTEM_PROMPT
    routes = (await session.execute(select(LLMRoute).order_by(LLMRoute.task))).scalars().all()
    return {"settings": {k: await get_setting(session, k) for k in SNAPSHOT_KEYS},
            "models": [{"task": r.task, "primary": [r.primary_provider, r.primary_model],
                        "fallback": [r.fallback_provider, r.fallback_model] if r.fallback_provider else None}
                       for r in routes],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}


async def _cheapest_judge(session: AsyncSession):
    routes = (await session.execute(select(LLMRoute))).scalars().all()
    candidates = []
    for route in routes:
        for provider, model in ((route.primary_provider, route.primary_model),
                                (route.fallback_provider, route.fallback_model)):
            if not provider or not model:
                continue
            cfg = (await session.execute(select(LLMProviderConfig).where(
                LLMProviderConfig.name == provider, LLMProviderConfig.enabled.is_(True),
                LLMProviderConfig.api_key_enc.is_not(None)))).scalar_one_or_none()
            if not cfg:
                continue
            price = (await session.execute(select(LLMModel).where(
                LLMModel.provider == provider, LLMModel.model == model))).scalar_one_or_none()
            rank = float(price.input_price + price.output_price) if price else float("inf")
            candidates.append((rank, provider, model, cfg, price, route.max_tokens, route.temperature))
    if not candidates:
        raise RuntimeError("no configured model is available for evaluation judging")
    return min(candidates, key=lambda x: x[0])


async def judge(session: AsyncSession, llm: LLMService, case: EvalCase, actual: str, path: str,
                context: list[str]) -> tuple[bool, bool, str, Decimal, int, int]:
    _, provider_name, model, cfg, price, max_tokens, temperature = await _cheapest_judge(session)
    policy = ProviderPolicy(name=provider_name, allow_pii=cfg.allow_pii,
                            custom_patterns=tuple(await get_setting(session, "privacy_custom_patterns") or ()),
                            never_send=frozenset(await get_setting(session, "privacy_never_send") or ()),
                            name_mode=await get_setting(session, "name_mode") or "placeholder")
    content = json.dumps({"question": case.question, "expected_answer": case.expected_answer,
                          "actual_answer": actual, "path": path, "retrieved_context": context}, ensure_ascii=False)
    request = PrivacyGuard(policy).prepare_task(JUDGE_SYSTEM, content)
    provider = llm.factory(provider_name, crypto.decrypt(cfg.api_key_enc, aad=f"llm:{provider_name}"),
                           cfg.base_url, cfg.timeout_s, int(await get_setting(session, "llm_retries") or 0))
    started = time.perf_counter()
    result = await provider.generate(request, model=model, max_tokens=min(max_tokens, 300), temperature=0)
    cost = cost_of(price, result.tokens_in, result.tokens_out)
    await llm._log(task="evaluation_judge", channel="eval", conversation_id=None, provider=provider_name, model=model,
                   status="ok", tokens_in=result.tokens_in, tokens_out=result.tokens_out, cost_usd=cost,
                   latency_ms=result.latency_ms or int((time.perf_counter() - started) * 1000), fallback_used=False,
                   budget_fallback=False, is_test=True, redactions=request.report.get("redactions"), request_json=request.audit_payload)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.text.strip(), flags=re.I)
    data = json.loads(raw)
    return bool(data["correct"]), bool(data["grounded"]), str(data.get("reason", ""))[:1000], cost, result.tokens_in, result.tokens_out


async def run_eval(maker: async_sessionmaker[AsyncSession], run_id: int, *, llm: LLMService | None = None) -> dict:
    llm = llm or LLMService(maker)
    async with maker() as session:
        run = await session.get(EvalRun, run_id)
        if not run:
            return {"status": "missing"}
        cases = (await session.execute(select(EvalCase).where(EvalCase.active.is_(True)).order_by(EvalCase.id))).scalars().all()
        run.status, run.total, run.completed, run.error = "running", len(cases), 0, None
        await session.commit()
        case_ids = [c.id for c in cases]
    try:
        for case_id in case_ids:
            async with maker() as session:
                case, run = await session.get(EvalCase, case_id), await session.get(EvalRun, run_id)
                started = time.perf_counter()
                try:
                    history = [(item["role"], item["content"]) for item in (case.history or [])
                               if item.get("role") in ("user", "assistant") and item.get("content")]
                    answer = await run_pipeline(session, llm, text=case.question, profile=None, conversation=None,
                                                channel="eval", history=history, persist=False, is_test=True)
                    debug = answer.debug or {}
                    chunks = debug.get("chunks") or []
                    missed = answer.path == "no_context" and bool(case.expected_answer.strip())
                    answer_cost = Decimal(str(debug.get("cost_usd") or 0))
                    judge_cost, judge_in, judge_out = Decimal(0), 0, 0
                    if missed:
                        correct, grounded, reason = False, True, "Expected answer exists, but retrieval returned no_context."
                    else:
                        correct, grounded, reason, judge_cost, judge_in, judge_out = await judge(
                            session, llm, case, answer.text, answer.path, [c.get("preview", "") for c in chunks])
                    if case.expected_path and answer.path != case.expected_path:
                        correct = False
                        reason = f"Expected path {case.expected_path}, got {answer.path}. {reason}".strip()
                    session.add(EvalResult(run_id=run_id, case_id=case.id, actual_answer=answer.text, path=answer.path,
                                           latency_ms=int((time.perf_counter() - started) * 1000),
                                           tokens_in=(answer.tokens_in or 0) + judge_in, tokens_out=(answer.tokens_out or 0) + judge_out,
                                           cost_usd=answer_cost, judge_cost_usd=judge_cost,
                                           retrieved_chunk_ids=[c["chunk_id"] for c in chunks], correct=correct,
                                           grounded=grounded, missed=missed, reason=reason))
                except Exception as exc:  # one bad case must not discard the rest of the run
                    session.add(EvalResult(run_id=run_id, case_id=case.id, actual_answer="", path="error",
                                           latency_ms=int((time.perf_counter() - started) * 1000), correct=False,
                                           grounded=False, missed=False, reason=f"{type(exc).__name__}: {exc}"[:1000]))
                run.completed += 1
                await session.commit()
        async with maker() as session:
            run = await session.get(EvalRun, run_id)
            run.status, run.finished_at = "done", datetime.now(timezone.utc)
            await session.commit()
            return {"status": "done", "run_id": run_id, "total": run.total}
    except Exception as exc:
        async with maker() as session:
            run = await session.get(EvalRun, run_id)
            run.status, run.error, run.finished_at = "failed", str(exc)[:1000], datetime.now(timezone.utc)
            await session.commit()
        raise
