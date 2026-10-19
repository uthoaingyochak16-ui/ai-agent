"""Routing, fallback, budget, cost logging and the outgoing-request audit trail."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import crypto
from app.llm.providers import PROVIDER_CLASSES, LLMError, LLMProvider, LLMResult
from app.models import LLMCall, LLMModel, LLMProviderConfig, LLMRoute
from app.privacy.guard import GuardedRequest, ProviderPolicy
from app.services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)

# tests can replace this to inject fake providers
ProviderFactory = Callable[[str, str, str | None, float, int], LLMProvider]


def default_factory(name: str, api_key: str, base_url: str | None, timeout_s: float, retries: int) -> LLMProvider:
    return PROVIDER_CLASSES[name](api_key, base_url=base_url, timeout_s=timeout_s, retries=retries)


class LLMUnavailable(Exception):
    """No provider could answer (all failed, none configured, or the hard budget stop is on)."""


@dataclass
class LLMOutcome:
    result: LLMResult
    request: GuardedRequest     # the request actually sent (policy of the provider that answered)
    cost_usd: Decimal
    call_id: int
    fallback_used: bool
    budget_fallback: bool


@dataclass
class BudgetState:
    spent: Decimal
    budget: Decimal
    percent: float
    level: str  # ok | alert | exceeded | unlimited


def month_start(now: datetime | None = None) -> datetime:
    n = now or datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def budget_state(session: AsyncSession) -> BudgetState:
    spent = (await session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(
        LLMCall.created_at >= month_start()))).scalar_one()
    budget = Decimal(str(await get_setting(session, "monthly_budget_usd") or 0))
    if budget <= 0:
        return BudgetState(Decimal(spent), budget, 0.0, "unlimited")
    pct = float(Decimal(spent) / budget * 100)
    alert = float(await get_setting(session, "budget_alert_percent") or 80)
    level = "exceeded" if pct >= 100 else "alert" if pct >= alert else "ok"
    return BudgetState(Decimal(spent), budget, pct, level)


def cost_of(model: LLMModel | None, tin: int, tout: int) -> Decimal:
    if model is None:
        return Decimal(0)
    return (Decimal(tin) * model.input_price + Decimal(tout) * model.output_price) / Decimal(1_000_000)


class LLMService:
    def __init__(self, maker: async_sessionmaker[AsyncSession], factory: ProviderFactory | None = None):
        self.maker = maker
        self.factory = factory or default_factory

    async def _log(self, **kw) -> int:
        async with self.maker() as s:  # separate transaction: costs are logged even if the caller rolls back
            row = LLMCall(**kw)
            s.add(row)
            await s.commit()
            return row.id

    async def run(self, session: AsyncSession, task: str, build: Callable[[ProviderPolicy], GuardedRequest], *,
                  channel: str | None = None, conversation_id: int | None = None, is_test: bool = False,
                  tools: list | None = None) -> LLMOutcome:
        """`build(policy)` is called once per provider attempt so a fallback provider gets ITS OWN privacy policy."""
        route = (await session.execute(select(LLMRoute).where(LLMRoute.task == task))).scalar_one_or_none()
        if route is None:
            raise LLMUnavailable(f"no route configured for task '{task}'")
        candidates = [(route.primary_provider, route.primary_model)]
        if route.fallback_provider and route.fallback_model:
            candidates.append((route.fallback_provider, route.fallback_model))

        state = await budget_state(session)
        budget_mode = False
        if state.level == "exceeded":
            if await get_setting(session, "budget_hard_stop"):
                raise LLMUnavailable("monthly budget exhausted (hard stop)")
            cheap = await get_setting(session, "budget_fallback_route")
            if await get_setting(session, "budget_fallback_enabled") and cheap:
                candidates, budget_mode = [(cheap["provider"], cheap["model"])] + candidates, True
        if state.level in ("alert", "exceeded"):
            marker = f"{month_start():%Y-%m}:{state.level}"
            if await get_setting(session, "budget_alert_marker") != marker:
                logger.warning("LLM budget %s: %.0f%% of %s used", state.level, state.percent, state.budget)
                await set_setting(session, "budget_alert_marker", marker)
                await session.commit()

        retries = int(await get_setting(session, "llm_retries") or 0)
        policy_extra = {"custom_patterns": tuple(await get_setting(session, "privacy_custom_patterns") or ()),
                        "never_send": frozenset(await get_setting(session, "privacy_never_send") or ()),
                        "name_mode": await get_setting(session, "name_mode") or "placeholder"}
        last_err: Exception | None = None
        for idx, (pname, model) in enumerate(candidates):
            cfg = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == pname))).scalar_one_or_none()
            if cfg is None or not cfg.enabled or not cfg.api_key_enc:
                last_err = LLMError(f"provider '{pname}' is not configured")
                continue
            price = (await session.execute(select(LLMModel).where(LLMModel.provider == pname, LLMModel.model == model))).scalar_one_or_none()
            req = build(ProviderPolicy(name=pname, allow_pii=cfg.allow_pii, **policy_extra))
            provider = self.factory(pname, crypto.decrypt(cfg.api_key_enc, aad=f"llm:{pname}"), cfg.base_url, cfg.timeout_s, retries)
            t0 = time.perf_counter()
            common = dict(task=task, channel=channel, conversation_id=conversation_id, provider=pname, model=model,
                          fallback_used=idx > 0 and not budget_mode, budget_fallback=budget_mode and idx == 0,
                          is_test=is_test, redactions=req.report.get("redactions"), request_json=req.audit_payload)
            try:
                extra = {"tools": tools} if tools else {}  # only tool-aware calls pass it (keeps test fakes simple)
                res = await provider.generate(req, model=model, max_tokens=route.max_tokens, temperature=route.temperature, **extra)
            except LLMError as exc:
                last_err = exc
                await self._log(**common, status="error", error=str(exc)[:500], latency_ms=int((time.perf_counter() - t0) * 1000))
                logger.warning("LLM attempt failed (%s/%s): %s", pname, model, exc)
                continue
            cost = cost_of(price, res.tokens_in, res.tokens_out)
            call_id = await self._log(**common, status="ok", tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                                      cost_usd=cost, latency_ms=res.latency_ms)
            return LLMOutcome(res, req, cost, call_id, common["fallback_used"], common["budget_fallback"])
        raise LLMUnavailable(str(last_err) if last_err else "no provider available")
