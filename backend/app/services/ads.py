"""Ads, the safe way: the AI prepares ad drafts (goal, audience, budget, text) from a post or a program; a person reviews
them and starts the ad in Meta Ads Manager. Nothing here creates an ad or spends money.

Results: with an ad account id and an access token that may read it (ads_read), the last 30 days of each campaign
(spend, reach, impressions, clicks, results) are shown read-only.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.graph import GraphError, graph_request
from app.core import crypto
from app.core.config import get_settings
from app.llm.service import LLMService, LLMUnavailable
from app.models import AdDraft, SocialPost
from app.privacy.guard import PrivacyGuard
from app.rag.programs import context_line, today_bd, upcoming
from app.services import posts as P
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting

OBJECTIVES = ("awareness", "engagement", "messages", "traffic", "leads")
AD_SYSTEM = """You are the ads planner of Quantum Foundation (a non-profit in Bangladesh: Quantum Method meditation
courses, programs, blood donation ...). Prepare ONE Meta (Facebook / Instagram) ad from the SOURCE (a post or a
program) for the GOAL, within the BUDGET (Bangladeshi taka per day, if given; else suggest a modest one). Choose the
objective (awareness | engagement | messages | traffic | leads), a realistic audience in Bangladesh (age range,
genders, cities / districts, a few interests), the number of days, and the ad text (Bangla unless the source is
English; facts only from SOURCE). Explain briefly why. Return ONLY JSON:
{"name": "...", "objective": "...", "audience": {"age_min": 18, "age_max": 65, "genders": ["all"], "locations": ["Dhaka"], "interests": ["Meditation"]},
 "daily_budget": 500, "days": 7, "text": "...", "note": "why this audience, budget and objective"}"""


async def suggest(session: AsyncSession, llm: LLMService, *, goal: str, post_id: int | None = None, program_id: int | None = None,
                  daily_budget: float | None = None, by: str | None = None) -> AdDraft:
    source = ""
    post = await session.get(SocialPost, post_id) if post_id else None
    if post is not None:
        source = f"POST ({post.channel}, engagement {P.engagement(post.metrics)}): {post.message[:1500]}"
    if program_id:
        prog = next((p for p in await upcoming(session) if p.id == program_id), None)
        if prog is not None:
            source += f"\nPROGRAM: {context_line(prog, today_bd())}"
    content = f"GOAL: {goal[:500]}\nBUDGET: {daily_budget or 'not given'}\nSOURCE:\n{source or '(none: the organisation in general)'}"
    try:
        out = await llm.run(session, "answer", lambda pol: PrivacyGuard(pol).prepare_task(AD_SYSTEM, content), channel=None, is_test=True)
        d = parse_json_object(out.request.redactor.restore(out.result.text)) or {}
    except LLMUnavailable as exc:
        d = {"name": goal[:100], "note": f"No AI model is available ({exc}); fill this draft in yourself."}
    aud = d.get("audience") if isinstance(d.get("audience"), dict) else {}
    try:
        budget = float(d.get("daily_budget")) if d.get("daily_budget") is not None else daily_budget
    except (TypeError, ValueError):
        budget = daily_budget
    a = AdDraft(name=str(d.get("name") or goal)[:160], objective=d.get("objective") if d.get("objective") in OBJECTIVES else "engagement",
                post_id=post.id if post else None, program_id=program_id, text=str(d.get("text") or (post.message if post else ""))[:5000],
                audience={k: aud[k] for k in ("age_min", "age_max", "genders", "locations", "interests") if k in aud},
                daily_budget=budget, days=int(d["days"]) if str(d.get("days", "")).isdigit() else 7,
                note=str(d.get("note") or "")[:2000] or None, created_by=by)
    session.add(a)
    await session.flush()
    return a


async def results(session: AsyncSession) -> dict:
    account = (await get_setting(session, "ads_account_id") or "").strip().removeprefix("act_")
    token_enc = await get_setting(session, "ads_access_token_enc")
    if not account:
        return {"configured": False, "reason": "No ad account id is set (AI settings → ads_account_id)."}
    manager = f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={account}"
    if not token_enc:
        return {"configured": False, "reason": "No ads access token is saved.", "ads_manager": manager}
    s = get_settings()
    try:
        d = await graph_request("GET", f"act_{account}/insights", token=crypto.decrypt(token_enc, aad="ads:token"),
                                base_url=s.graph_base_url, version=s.graph_api_version,
                                params={"level": "campaign", "date_preset": "last_30d", "limit": 50,
                                        "fields": "campaign_name,spend,reach,impressions,clicks,ctr,actions"})
    except GraphError as exc:
        return {"configured": True, "error": exc.message, "ads_manager": manager}
    rows = [{"campaign": r.get("campaign_name"), "spend": r.get("spend"), "reach": r.get("reach"), "impressions": r.get("impressions"),
             "clicks": r.get("clicks"), "ctr": r.get("ctr"),
             "results": sum(int(a.get("value", 0)) for a in r.get("actions") or [] if str(a.get("value", "")).isdigit())}
            for r in d.get("data", [])]
    return {"configured": True, "campaigns": rows, "ads_manager": manager}
