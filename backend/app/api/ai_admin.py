from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only, any_staff
from app.core import crypto
from app.db.session import get_session
from app.llm.providers import PROVIDER_CLASSES, LLMError
from app.llm.service import budget_state
from app.models import LLMModel, LLMProviderConfig, LLMRoute
from app.privacy.guard import PrivacyGuard, ProviderPolicy
from app.services.settings_service import DEFAULTS, get_setting, set_setting

router = APIRouter(prefix="/api/ai", tags=["ai settings"], dependencies=[Depends(admin_only)])
TASKS = ("answer", "answer_large", "extraction", "summarization")

# key -> (type, validator)
_num = lambda lo, hi: (lambda v: lo <= v <= hi)  # noqa: E731
SETTING_SPECS: dict[str, tuple[tuple, object]] = {
    "monthly_budget_usd": ((int, float), _num(0, 1e7)), "budget_alert_percent": ((int, float), _num(1, 100)),
    "budget_fallback_enabled": ((bool,), None), "budget_hard_stop": ((bool,), None),
    "budget_fallback_route": ((dict, type(None)), lambda v: v is None or (isinstance(v.get("provider"), str) and isinstance(v.get("model"), str))),
    "top_k": ((int,), _num(1, 20)), "history_len": ((int,), _num(0, 20)), "chunk_token_budget": ((int,), _num(100, 8000)),
    "qa_threshold": ((int, float), _num(0.5, 1)), "cache_threshold": ((int, float), _num(0.5, 1)), "cache_ttl_days": ((int, float), _num(0, 365)),
    "retrieval_min_similarity": ((int, float), _num(0, 1)), "escalate_below": ((int, float), _num(0, 1)),
    "extraction_every_n_messages": ((int,), _num(0, 100)), "summarize_every_n_messages": ((int,), _num(2, 100)),
    "llm_retries": ((int,), _num(0, 5)),
    "name_mode": ((str,), lambda v: v in ("placeholder", "first_name", "none")),
    "merge_policy": ((str,), lambda v: v in ("auto", "suggest")), "human_agent_tag_enabled": ((bool,), None),
    "privacy_custom_patterns": ((list,), lambda v: all(isinstance(x, str) and _valid_re(x) for x in v)),
    "privacy_never_send": ((list,), lambda v: all(x in ("name", "location", "age", "profession", "language") for x in v)),
    "handover_keywords": ((list,), lambda v: all(isinstance(x, str) and x.strip() for x in v)),
    "handover_ack": ((str,), None), "erasure_ack": ((str,), None), "no_answer_reply": ((str,), None), "consent_prompt": ((str,), None),
    "handover_ack_en": ((str,), None), "erasure_ack_en": ((str,), None), "no_answer_reply_en": ((str,), None),
    "consent_prompt_en": ((str,), None), "greeting_repeat_reply": ((str,), lambda v: bool(v.strip())),
    "system_prompt": ((str, type(None)), None),
    "greeting_enabled": ((bool,), None), "greeting_first_reply": ((str,), lambda v: bool(v.strip())),
    "greeting_return_reply": ((str,), lambda v: bool(v.strip())), "greeting_gap_hours": ((int, float), _num(0, 720)),
    "greeting_words": ((list,), lambda v: all(isinstance(x, str) and x.strip() for x in v)),
    "conversation_retention_days": ((int,), _num(1, 3650)),
    "smalltalk_enabled": ((bool,), None),
    "chunk_words": ((int,), _num(30, 1000)), "chunk_overlap_words": ((int,), _num(0, 200)),
    "max_answer_chars": ((int,), _num(0, 4000)), "qa_text_threshold": ((int, float), _num(0.5, 1)),
    "ai_auto_resume_minutes": ((int, float), _num(0, 10080)),
    "smalltalk": ((list,), lambda v: len(v) <= 200 and all(
        isinstance(x, dict) and isinstance(x.get("answer"), str) and x["answer"].strip() and len(x["answer"]) <= 2000
        and isinstance(x.get("answer_en", ""), str) and len(x.get("answer_en", "")) <= 2000
        and isinstance(x.get("questions"), list) and x["questions"]
        and all(isinstance(q, str) and q.strip() and len(q) <= 200 for q in x["questions"]) for x in v)),
    "embedding_provider": ((str,), lambda v: v in ("hash", "openai", "google")), "embedding_model": ((str,), None),
}


def _valid_re(p: str) -> bool:
    try:
        re.compile(p)
        return len(p) <= 200
    except re.error:
        return False


@router.get("/settings")
async def get_settings_api(session: AsyncSession = Depends(get_session)):
    return {k: await get_setting(session, k) for k in SETTING_SPECS} | {"_defaults": {k: DEFAULTS.get(k) for k in SETTING_SPECS}}


@router.put("/settings")
async def put_settings(body: dict, session: AsyncSession = Depends(get_session)):
    for k, v in body.items():
        if k not in SETTING_SPECS:
            raise HTTPException(422, f"Unknown setting: {k}")
        types, check = SETTING_SPECS[k]
        if isinstance(v, bool) and bool not in types or not isinstance(v, types) or (check and not check(v)):
            raise HTTPException(422, f"Invalid value for {k}")
    for k, v in body.items():
        await set_setting(session, k, v)
    await session.commit()
    return await get_settings_api(session)


@router.get("/budget")
async def budget(session: AsyncSession = Depends(get_session)):
    b = await budget_state(session)
    return {"spent": float(b.spent), "budget": float(b.budget), "percent": round(b.percent, 1), "level": b.level}


# ---- providers -----------------------------------------------------------------------------------------------------
def _prov(name: str, c: LLMProviderConfig | None) -> dict:
    return {"name": name, "enabled": bool(c and c.enabled), "has_key": bool(c and c.api_key_enc), "allow_pii": bool(c and c.allow_pii),
            "base_url": (c.base_url if c and c.base_url else PROVIDER_CLASSES[name].default_base),
            "timeout_s": c.timeout_s if c else 30.0}


@router.get("/providers")
async def providers(session: AsyncSession = Depends(get_session)):
    rows = {c.name: c for c in (await session.execute(select(LLMProviderConfig))).scalars()}
    return [_prov(n, rows.get(n)) for n in PROVIDER_CLASSES]


class ProviderIn(BaseModel):
    enabled: bool | None = None
    allow_pii: bool | None = None
    api_key: str | None = None   # write-only
    base_url: str | None = Field(None, max_length=255)
    timeout_s: float | None = Field(None, ge=1, le=120)


@router.put("/providers/{name}")
async def put_provider(name: str, body: ProviderIn, session: AsyncSession = Depends(get_session)):
    if name not in PROVIDER_CLASSES:
        raise HTTPException(404, "Unknown provider")
    c = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == name))).scalar_one_or_none()
    if c is None:
        c = LLMProviderConfig(name=name)
        session.add(c)
    for f in ("enabled", "allow_pii", "timeout_s"):
        if getattr(body, f) is not None:
            setattr(c, f, getattr(body, f))
    if body.base_url is not None:
        c.base_url = body.base_url or None
    if body.api_key:
        c.api_key_enc = crypto.encrypt(body.api_key, aad=f"llm:{name}")
    await session.commit()
    return _prov(name, c)


class TestIn(BaseModel):
    model: str


@router.post("/providers/{name}/test")
async def test_provider(name: str, body: TestIn, session: AsyncSession = Depends(get_session)):
    c = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == name))).scalar_one_or_none()
    if not c or not c.api_key_enc:
        raise HTTPException(409, "Save an API key first")
    prov = PROVIDER_CLASSES[name](crypto.decrypt(c.api_key_enc, aad=f"llm:{name}"), base_url=c.base_url, timeout_s=c.timeout_s, retries=0)
    req = PrivacyGuard(ProviderPolicy(name=name)).prepare_task("Reply with the single word: OK", "ping")
    try:
        res = await prov.generate(req, model=body.model, max_tokens=16, temperature=0)
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "reply": res.text[:40], "latency_ms": res.latency_ms}


# ---- prices ---------------------------------------------------------------------------------------------------------
class ModelIn(BaseModel):
    provider: str
    model: str = Field(min_length=1, max_length=96)
    input_price: float = Field(ge=0)
    output_price: float = Field(ge=0)
    price_verified: bool = False
    active: bool = True


@router.get("/models")
async def models(session: AsyncSession = Depends(get_session)):
    return [{"id": m.id, "provider": m.provider, "model": m.model, "input_price": float(m.input_price), "output_price": float(m.output_price),
             "price_verified": m.price_verified, "active": m.active}
            for m in (await session.execute(select(LLMModel).order_by(LLMModel.provider, LLMModel.model))).scalars()]


@router.put("/models")
async def put_model(body: ModelIn, session: AsyncSession = Depends(get_session)):
    if body.provider not in PROVIDER_CLASSES:
        raise HTTPException(422, "Unknown provider")
    m = (await session.execute(select(LLMModel).where(LLMModel.provider == body.provider, LLMModel.model == body.model))).scalar_one_or_none()
    if m is None:
        m = LLMModel(provider=body.provider, model=body.model)
        session.add(m)
    m.input_price, m.output_price, m.price_verified, m.active = body.input_price, body.output_price, body.price_verified, body.active
    await session.commit()
    return {"id": m.id}


@router.delete("/models/{mid}")
async def del_model(mid: int, session: AsyncSession = Depends(get_session)):
    m = await session.get(LLMModel, mid)
    if m:
        await session.delete(m)
        await session.commit()
    return {"ok": True}


# ---- routes -----------------------------------------------------------------------------------------------------------
class RouteIn(BaseModel):
    primary_provider: str
    primary_model: str = Field(min_length=1)
    fallback_provider: str | None = None
    fallback_model: str | None = None
    max_tokens: int = Field(400, ge=16, le=8000)
    temperature: float = Field(0.2, ge=0, le=2)


def _route(task: str, r: LLMRoute | None) -> dict:
    if r is None:
        return {"task": task, "configured": False}
    return {"task": task, "configured": True, "primary_provider": r.primary_provider, "primary_model": r.primary_model,
            "fallback_provider": r.fallback_provider, "fallback_model": r.fallback_model, "max_tokens": r.max_tokens, "temperature": r.temperature}


@router.get("/routes")
async def routes(session: AsyncSession = Depends(get_session)):
    rows = {r.task: r for r in (await session.execute(select(LLMRoute))).scalars()}
    return [_route(t, rows.get(t)) for t in TASKS]


@router.put("/routes/{task}")
async def put_route(task: str, body: RouteIn, session: AsyncSession = Depends(get_session)):
    if task not in TASKS:
        raise HTTPException(404, "Unknown task")
    for p in (body.primary_provider, body.fallback_provider):
        if p and p not in PROVIDER_CLASSES:
            raise HTTPException(422, f"Unknown provider: {p}")
    r = (await session.execute(select(LLMRoute).where(LLMRoute.task == task))).scalar_one_or_none()
    if r is None:
        r = LLMRoute(task=task)
        session.add(r)
    for k, v in body.model_dump().items():
        setattr(r, k, v or None if k.startswith("fallback") else v)
    await session.commit()
    return _route(task, r)
