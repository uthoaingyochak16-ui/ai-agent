from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.services.embedding_maintenance import embedding_status

router = APIRouter(prefix="/api/ai", tags=["ai settings"], dependencies=[Depends(admin_only)])
TASKS = ("answer", "answer_large", "rewrite", "keywords", "keygen", "router", "extraction", "summarization")
_TASK_DEFAULTS = {"rewrite": (80, 0.0), "keywords": (120, 0.0), "keygen": (300, 0.0), "router": (160, 0.0)}  # (max_tokens, temperature) shown for an unset route

# key -> (type, validator)
_num = lambda lo, hi: (lambda v: lo <= v <= hi)  # noqa: E731
_STYLE_TEXT = ("tone", "template", "template_en", "followup", "followup_en")


def _valid_styles(v: dict) -> bool:
    """reply_styles: {question type: {tone, template(_en) with {answer}, followup(_en), handover none|offer|auto}}."""
    from app.services.reply_style import HANDOVER_RULES, TYPES
    for qtype, s in v.items():
        if qtype not in TYPES or not isinstance(s, dict) or set(s) - set(_STYLE_TEXT) - {"handover"}:
            return False
        if any(not isinstance(s.get(k, ""), str) or len(s.get(k, "")) > 1000 for k in _STYLE_TEXT):
            return False
        if any(s.get(k, "").strip() and "{answer}" not in s[k] for k in ("template", "template_en")):
            return False
        if s.get("handover", "none") not in HANDOVER_RULES:
            return False
    return True


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
    "greeting_first_reply_en": ((str,), None), "greeting_return_reply_en": ((str,), None),
    "greeting_repeat_reply_en": ((str,), None),
    "greeting_words": ((list,), lambda v: all(isinstance(x, str) and x.strip() for x in v)),
    "conversation_retention_days": ((int,), _num(1, 3650)),
    "smalltalk_enabled": ((bool,), None),
    "chunk_words": ((int,), _num(30, 1000)), "chunk_overlap_words": ((int,), _num(0, 200)),
    "max_answer_chars": ((int,), _num(0, 4000)), "qa_text_threshold": ((int, float), _num(0.5, 1)),
    "ai_auto_resume_minutes": ((int, float), _num(0, 10080)),
    "router_enabled": ((bool,), None),
    "general_answer_enabled": ((bool,), None),
    "keyword_search_enabled": ((bool,), None), "keyword_ai_enabled": ((bool,), None),
    "keyword_ai_skip_above": ((int, float), _num(0, 1)), "keyword_min_coverage": ((int, float), _num(0.1, 1)),
    "keyword_top_k": ((int,), _num(1, 10)),
    "tools_enabled": ((dict,), lambda v: all(k in ("create_lead", "request_callback", "current_datetime", "search_knowledge", "search_sheet") and isinstance(x, bool) for k, x in v.items())),
    **{k: ((str,), lambda v: bool(v.strip())) for k in ("tool_confirm_prompt", "tool_declined_ack", "tool_lead_ack", "tool_callback_ack")},
    **{f"{k}_en": ((str,), None) for k in ("tool_confirm_prompt", "tool_declined_ack", "tool_lead_ack", "tool_callback_ack")},
    "alert_webhook_url": ((str,), lambda v: not v.strip() or v.strip().startswith("https://")),
    "alert_stuck_minutes": ((int, float), _num(1, 1440)), "alert_failed_sends_per_hour": ((int,), _num(0, 10000)),
    "erasure_confirm_prompt": ((str,), lambda v: bool(v.strip())), "erasure_confirm_prompt_en": ((str,), None),
    "consent_yes_ack": ((str,), lambda v: bool(v.strip())), "consent_yes_ack_en": ((str,), None),
    "consent_no_ack": ((str,), lambda v: bool(v.strip())), "consent_no_ack_en": ((str,), None),
    "lead_capture_enabled": ((bool,), None), "info_collection_enabled": ((bool,), None),
    **{k: ((str,), lambda v: "{fields}" in v) for k in ("lead_prompt",)},
    **{f"{k}_en": ((str,), lambda v: not v.strip() or "{fields}" in v) for k in ("lead_prompt",)},
    "lead_thanks": ((str,), lambda v: bool(v.strip())), "lead_thanks_en": ((str,), None),
    "lead_declined_ack": ((str,), lambda v: bool(v.strip())), "lead_declined_ack_en": ((str,), None),
    "program_followup_enabled": ((bool,), None),
    **{k: ((str,), lambda v: bool(v.strip())) for k in ("program_district_prompt", "program_nearby_none", "handover_offer",
                                                    "program_nearby_list_intro")},
    "program_nearby_reply": ((str,), lambda v: "{program_name}" in v and "{place}" in v),
    **{f"{k}_en": ((str,), None) for k in ("program_district_prompt", "program_nearby_reply", "program_nearby_none", "handover_offer",
                                        "program_nearby_list_intro")},
    "reply_styles": ((dict,), _valid_styles),
    **{k: ((str,), lambda v: bool(v.strip())) for k in ("lead_complete", "lead_anything_else", "data_saving_reply")},
    **{f"{k}_en": ((str,), None) for k in ("lead_complete", "lead_anything_else", "data_saving_reply")},
    "lead_explain": ((str,), lambda v: "{fields}" in v), "lead_explain_en": ((str,), lambda v: not v.strip() or "{fields}" in v),
    "card_warning": ((str,), lambda v: bool(v.strip())), "card_warning_en": ((str,), None),
    "debounce_seconds": ((int, float), _num(0, 30)), "debounce_max_seconds": ((int, float), _num(1, 120)),
    "reply_max_lines": ((int,), _num(0, 40)),
    "smalltalk": ((list,), lambda v: len(v) <= 200 and all(
        isinstance(x, dict) and isinstance(x.get("answer"), str) and x["answer"].strip() and len(x["answer"]) <= 2000
        and isinstance(x.get("answer_en", ""), str) and len(x.get("answer_en", "")) <= 2000
        and isinstance(x.get("questions"), list) and x["questions"]
        and all(isinstance(q, str) and q.strip() and len(q) <= 200 for q in x["questions"]) for x in v)),
    "embedding_provider": ((str,), lambda v: v in ("hash", "openai", "google", "local")), "embedding_model": ((str,), None),
    "rerank_provider": ((str,), lambda v: v in ("none", "cohere", "local")), "rerank_model": ((str,), None),
    "rerank_candidates": ((int,), _num(1, 80)),
    "rerank_min_score": ((int, float, type(None)), lambda v: v is None or 0 <= v <= 1),
    "comment_mode": ((str,), lambda v: v in ("off", "review", "auto")),
    **{k: ((bool,), None) for k in ("post_approval_required", "auto_program_drafts", "auto_plan_weekly", "weekly_report_enabled")},
    "report_language": ((str,), lambda v: v in ("bn", "en")),
    "ads_account_id": ((str,), lambda v: len(v) <= 40 and v.replace("act_", "").strip().isdigit() or not v.strip()),
    **{k: ((bool,), None) for k in ("comment_auto_like", "comment_reply_to_praise", "comment_private_reply")},
    "comment_spam_action": ((str,), lambda v: v in ("ignore", "hide", "delete")),
    "comment_against_action": ((str,), lambda v: v in ("notify", "hide", "delete")),
    "comment_remove_min_confidence": ((int, float), _num(0, 1)),
    "comment_reply_delay_seconds": ((int, float), _num(0, 600)), "comment_max_replies_per_hour": ((int,), _num(0, 1000)),
    "comment_thread_max_replies": ((int,), _num(0, 10)), "comment_max_chars": ((int,), _num(80, 1000)),
    **{k: ((str,), lambda v: bool(v.strip()) and len(v) <= 1000) for k in (
        "comment_no_answer", "comment_thanks_reply", "comment_complaint_reply", "comment_private_reply_text")},
    **{f"{k}_en": ((str,), lambda v: len(v) <= 1000) for k in (
        "comment_no_answer", "comment_thanks_reply", "comment_complaint_reply", "comment_private_reply_text")},
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


class LocalEmbeddingIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str | None = None
    enabled: bool = True


@router.get("/embeddings/status")
async def embeddings_status(session: AsyncSession = Depends(get_session)):
    out = await embedding_status(session)
    local = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == "local"))).scalar_one_or_none()
    out["local"] = {"enabled": bool(local and local.enabled), "base_url": local.base_url if local else None,
                    "has_key": bool(local and local.api_key_enc)}
    return out


@router.put("/embeddings/local")
async def put_local_embedding(body: LocalEmbeddingIn, session: AsyncSession = Depends(get_session)):
    cfg = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == "local"))).scalar_one_or_none()
    if cfg is None:
        cfg = LLMProviderConfig(name="local")
        session.add(cfg)
    cfg.enabled, cfg.base_url, cfg.allow_pii = body.enabled, body.base_url.rstrip("/"), False
    if body.api_key:
        cfg.api_key_enc = crypto.encrypt(body.api_key, aad="llm:local")
    await session.commit()
    return {"enabled": cfg.enabled, "base_url": cfg.base_url, "has_key": bool(cfg.api_key_enc)}


class RerankProviderIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str | None = None
    enabled: bool = True
    allow_pii: bool = False
    timeout_s: float = Field(30, ge=1, le=120)


@router.get("/rerank/providers")
async def rerank_providers(session: AsyncSession = Depends(get_session)):
    rows = {c.name: c for c in (await session.execute(select(LLMProviderConfig).where(
        LLMProviderConfig.name.in_(("rerank_cohere", "rerank_local"))))).scalars()}
    result = []
    for name, default_url in (("cohere", "https://api.cohere.com"),
                              ("local", "http://host.docker.internal:7998")):
        cfg = rows.get(f"rerank_{name}")
        result.append({"name": name, "enabled": bool(cfg and cfg.enabled),
                       "base_url": cfg.base_url if cfg and cfg.base_url else default_url,
                       "has_key": bool(cfg and cfg.api_key_enc), "allow_pii": bool(cfg and cfg.allow_pii),
                       "timeout_s": cfg.timeout_s if cfg else 30})
    return result


@router.put("/rerank/providers/{name}")
async def put_rerank_provider(name: str, body: RerankProviderIn, session: AsyncSession = Depends(get_session)):
    if name not in ("cohere", "local"):
        raise HTTPException(404, "Unknown rerank provider")
    config_name = f"rerank_{name}"
    cfg = (await session.execute(select(LLMProviderConfig).where(LLMProviderConfig.name == config_name))).scalar_one_or_none()
    if cfg is None:
        cfg = LLMProviderConfig(name=config_name)
        session.add(cfg)
    cfg.enabled, cfg.base_url, cfg.allow_pii, cfg.timeout_s = body.enabled, body.base_url.rstrip("/"), body.allow_pii, body.timeout_s
    if body.api_key:
        cfg.api_key_enc = crypto.encrypt(body.api_key, aad=f"rerank:{name}")
    await session.commit()
    return {"name": name, "enabled": cfg.enabled, "base_url": cfg.base_url,
            "has_key": bool(cfg.api_key_enc), "allow_pii": cfg.allow_pii, "timeout_s": cfg.timeout_s}


@router.post("/embeddings/reembed")
async def start_reembed(request: Request, session: AsyncSession = Depends(get_session)):
    status = await embedding_status(session)
    if status["progress"].get("status") == "running":
        return {"queued": False, **status}
    queue = getattr(request.app.state, "queue", None)
    if queue is None:
        raise HTTPException(503, "Job queue unavailable")
    job = await queue.enqueue_job("reembed_all_job", _job_id=f"reembed-{status['version']}")
    return {"queued": bool(job), **status}


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
        mt, temp = _TASK_DEFAULTS.get(task, (400, 0.2))
        return {"task": task, "configured": False, "max_tokens": mt, "temperature": temp}
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
