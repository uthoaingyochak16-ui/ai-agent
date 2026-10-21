"""Social posts: write (with the AI), schedule and publish to Facebook Pages and Instagram accounts; read every post
back with its numbers; and analyse what works.

- publish_due (worker, every minute): scheduled posts whose time has come are published (status scheduled ->
  publishing -> published | failed). A post is never published twice: the row is locked and its status moved first.
- sync (worker, every 3 hours, and the "Refresh" button): the latest posts of each connected account, with reactions,
  comments, shares and the insights Meta still gives (reach, saves ...). Posts made outside this app come in as
  source "synced"; posts made here get their numbers. Untagged posts get a one-word topic from the AI (cheap route).
- analytics: engagement per post (reactions + comments + shares + saves), engagement rate (per reach, when known),
  the best weekday x hour slots (Bangladesh time), topics and formats that do best, top and weakest posts.
- advice / draft: the AI reads those numbers (and the upcoming programs) and says what to post when, or writes
  captions (or a reel / story script) for a brief. Nothing is posted by the AI on its own: a person schedules or
  publishes.
- program_drafts (daily) and auto_plan (weekly, when switched on, and the "Fill the next 2 weeks" button) only make
  DRAFTS with a suggested time and the reason; a person approves them.
- approval (setting post_approval_required): a non-admin's scheduled / published post waits as pending_approval until
  an admin approves it. Every change is kept in the post's history.
- A/B tests: the same idea in two or more versions (ab_group, ab_variant A/B/...); analytics says which one won.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels import page_posts as pp
from app.channels.graph import GraphError
from app.llm.service import LLMService, LLMUnavailable
from app.models import ChannelConfig, SocialPost
from app.privacy.guard import PrivacyGuard
from app.rag.programs import BD_TZ, context_line, today_bd, upcoming
from app.services.outbound import ChannelNotConfigured, get_credentials
from app.services.post_reply import parse_json_object

logger = logging.getLogger(__name__)
PLATFORMS = {"facebook": "messenger", "instagram": "instagram"}     # post platform -> the channel account holding the token
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def log(post: SocialPost, by: str | None, action: str) -> None:
    post.history = [*(post.history or []), {"at": datetime.now(timezone.utc).isoformat(), "by": by or "system", "action": action[:200]}][-50:]


def next_slot(label: str, after: datetime | None = None) -> datetime | None:
    """"Fri 20:00" (Bangladesh time) -> the next such moment, as UTC."""
    m = re.match(r"(?i)\s*(mon|tue|wed|thu|fri|sat|sun)\w*\s+(\d{1,2})(?::(\d{2}))?", label or "")
    if not m:
        return None
    day = [d.lower() for d in DAYS].index(m.group(1).lower())
    now = (after or datetime.now(timezone.utc)).astimezone(BD_TZ)
    t = now.replace(hour=min(23, int(m.group(2))), minute=int(m.group(3) or 0), second=0, microsecond=0)
    t += timedelta(days=(day - now.weekday()) % 7)
    if t <= now:
        t += timedelta(days=7)
    return t.astimezone(timezone.utc)


def engagement(m: dict | None) -> int:
    m = m or {}
    return int(sum(m.get(k) or 0 for k in ("reactions", "comments", "shares", "saves")))


def rate(m: dict | None) -> float | None:
    reach = (m or {}).get("reach")
    return round(engagement(m) / reach, 4) if reach else None


async def accounts(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel.in_(tuple(PLATFORMS.values())),
                                                              ChannelConfig.account_id.is_not(None)))).scalars().all()
    return [{"channel": "instagram" if c.channel == "instagram" else "facebook", "account_id": c.account_id,
             "label": c.label or c.account_id, "status": c.status, "has_token": bool(c.access_token_enc)} for c in rows]


async def _creds(session: AsyncSession, platform: str, account_id: str):
    return await get_credentials(session, PLATFORMS[platform], account_id=account_id, for_sending=False)


# ---- publishing --------------------------------------------------------------------------------------------------
async def publish(session: AsyncSession, post: SocialPost) -> None:
    """Publish one post now. Leaves it published (with its id and link) or failed (with Meta's reason)."""
    if post.channel == "instagram" and not post.image_url:
        post.status, post.error = "failed", "Instagram needs an image: add one, then publish again"
        return
    post.status, post.error = "publishing", None
    await session.commit()
    try:
        creds = await _creds(session, post.channel, post.account_id)
        if post.channel == "instagram":
            pid, link = await pp.publish_instagram(post.account_id, creds, caption=post.message, image_url=post.image_url)
        else:
            pid, link = await pp.publish_facebook(post.account_id, creds, message=post.message, link=post.link, image_url=post.image_url)
    except (GraphError, ChannelNotConfigured) as exc:
        post.status, post.error = "failed", (exc.message if isinstance(exc, GraphError) else f"no token for this account: {exc}")[:500]
        return
    post.external_id, post.permalink, post.status = pid, link, "published"
    post.published_at = datetime.now(timezone.utc)
    post.media_type = post.media_type or ("photo" if post.image_url else "link" if post.link else "text")


async def publish_due(maker: async_sessionmaker[AsyncSession]) -> int:
    n = 0
    now = datetime.now(timezone.utc)
    async with maker() as session:
        ids = (await session.execute(select(SocialPost.id).where(SocialPost.status == "scheduled", SocialPost.scheduled_at <= now)
                                     .order_by(SocialPost.scheduled_at).limit(20))).scalars().all()
    for pid in ids:
        async with maker() as session:
            post = (await session.execute(select(SocialPost).where(SocialPost.id == pid, SocialPost.status == "scheduled")
                                          .with_for_update(skip_locked=True))).scalar_one_or_none()
            if post is None:
                continue
            await publish(session, post)
            await session.commit()
            n += 1
    return n


# ---- reading posts back ------------------------------------------------------------------------------------------
async def sync(session: AsyncSession, llm: LLMService | None = None, *, limit: int = 50) -> dict:
    """Every connected account's latest posts and numbers. Returns per account what came in, or why it did not."""
    report: list[dict] = []
    now = datetime.now(timezone.utc)
    for acc in await accounts(session):
        if not acc["has_token"]:
            continue
        try:
            creds = await _creds(session, acc["channel"], acc["account_id"])
            fetch = pp.instagram_posts if acc["channel"] == "instagram" else pp.facebook_posts
            remote = await fetch(acc["account_id"], creds, limit=limit)
        except (GraphError, ChannelNotConfigured) as exc:
            report.append({"account": acc["label"], "channel": acc["channel"], "error": getattr(exc, "message", str(exc))[:300]})
            continue
        known = {p.external_id: p for p in (await session.execute(select(SocialPost).where(
            SocialPost.channel == acc["channel"], SocialPost.external_id.in_([r.external_id for r in remote])))).scalars()}
        new = 0
        for r in remote:
            p = known.get(r.external_id)
            if p is None:
                p = SocialPost(channel=acc["channel"], account_id=acc["account_id"], external_id=r.external_id, source="synced",
                               status="published", message=r.message, published_at=r.created, media_type=r.media_type,
                               permalink=r.permalink, image_url=r.image_url)
                session.add(p)
                new += 1
            p.metrics, p.metrics_at = r.metrics, now
            p.permalink = p.permalink or r.permalink
            p.media_type = p.media_type or r.media_type
        report.append({"account": acc["label"], "channel": acc["channel"], "posts": len(remote), "new": new})
    await session.flush()
    tagged = await tag_topics(session, llm) if llm is not None else 0
    return {"accounts": report, "tagged": tagged}


TOPIC_SYSTEM = """You label social media posts of Quantum Foundation (a non-profit in Bangladesh: Quantum Method
meditation courses, programs and events, blood donation, Quantum Lab, quotes and wisdom, health, family, charity ...).
For each numbered post give ONE short topic (1-3 English words, Title Case) that a social media manager would group
it under, reusing the same label for the same subject (e.g. "Course Invitation", "Event Reminder", "Quote",
"Blood Donation", "Health Tip", "Success Story", "Charity", "Live Session"). Return ONLY JSON: {"1": "Quote", ...}"""


async def tag_topics(session: AsyncSession, llm: LLMService, batch: int = 25) -> int:
    rows = (await session.execute(select(SocialPost).where(SocialPost.topic.is_(None), SocialPost.status == "published")
                                  .order_by(SocialPost.id.desc()).limit(batch))).scalars().all()
    rows = [r for r in rows if (r.message or "").strip()]
    if not rows:
        return 0
    content = "\n".join(f"{i}. {r.message[:400]}" for i, r in enumerate(rows, 1))
    try:
        out = await llm.run(session, "router", lambda pol: PrivacyGuard(pol).prepare_task(TOPIC_SYSTEM, content), channel=None)
    except LLMUnavailable:
        return 0
    d = parse_json_object(out.result.text) or {}
    n = 0
    for i, r in enumerate(rows, 1):
        t = d.get(str(i))
        if isinstance(t, str) and t.strip():
            r.topic, n = t.strip()[:60], n + 1
    return n


# ---- the analysis ------------------------------------------------------------------------------------------------
async def analytics(session: AsyncSession, *, days: int = 90, channel: str | None = None, account_id: str | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(SocialPost).where(SocialPost.status == "published", SocialPost.published_at >= since)
    if channel:
        q = q.where(SocialPost.channel == channel)
    if account_id:
        q = q.where(SocialPost.account_id == account_id)
    posts = (await session.execute(q)).scalars().all()
    if not posts:
        return {"posts": 0, "days": days}
    eng = {p.id: engagement(p.metrics) for p in posts}
    avg = sum(eng.values()) / len(posts)
    slots: dict[tuple[int, int], list[int]] = defaultdict(list)
    for p in posts:
        t = p.published_at.astimezone(BD_TZ)
        slots[(t.weekday(), t.hour)].append(eng[p.id])
    heat = [{"day": d, "hour": h, "posts": len(v), "avg": round(sum(v) / len(v), 1)} for (d, h), v in slots.items()]
    best = sorted([x for x in heat if x["posts"] >= 2] or heat, key=lambda x: -x["avg"])[:5]

    def group(key) -> list[dict]:
        g: dict[str, list[SocialPost]] = defaultdict(list)
        for p in posts:
            g[key(p) or "Other"].append(p)
        return sorted(({"name": k, "posts": len(v), "avg": round(sum(eng[p.id] for p in v) / len(v), 1),
                        "vs_average": round((sum(eng[p.id] for p in v) / len(v)) / avg, 2) if avg else None} for k, v in g.items()),
                      key=lambda x: -x["avg"])
    ranked = sorted(posts, key=lambda p: -eng[p.id])
    card = lambda p: {"id": p.id, "channel": p.channel, "message": (p.message or "")[:160], "permalink": p.permalink,  # noqa: E731
                      "published_at": p.published_at, "topic": p.topic, "media_type": p.media_type, "engagement": eng[p.id],
                      "rate": rate(p.metrics), "metrics": p.metrics}
    reach = [p.metrics.get("reach") for p in posts if (p.metrics or {}).get("reach")]
    groups: dict[str, list[SocialPost]] = defaultdict(list)
    for p in posts:
        if p.ab_group:
            groups[p.ab_group].append(p)
    ab_tests = []
    for g, ps in groups.items():
        variants = sorted(({"variant": p.ab_variant or "?", "id": p.id, "channel": p.channel, "message": (p.message or "")[:120],
                            "engagement": eng[p.id], "rate": rate(p.metrics)} for p in ps), key=lambda x: x["variant"])
        if len(variants) > 1:
            best_ = max(variants, key=lambda v: (v["rate"] or 0, v["engagement"]))
            ab_tests.append({"group": g, "variants": variants, "winner": best_["variant"],
                             "published_at": min(p.published_at for p in ps)})
    by_day = []
    for d in range(7):
        v = [eng[p.id] for p in posts if p.published_at.astimezone(BD_TZ).weekday() == d]
        by_day.append({"day": DAYS[d], "posts": len(v), "avg": round(sum(v) / len(v), 1) if v else 0})
    return {"posts": len(posts), "days": days, "total_engagement": sum(eng.values()), "avg_engagement": round(avg, 1),
            "avg_reach": round(sum(reach) / len(reach)) if reach else None,
            "heatmap": heat, "best_slots": [{**x, "label": f"{DAYS[x['day']]} {x['hour']:02d}:00"} for x in best],
            "by_day": by_day,
            "topics": group(lambda p: p.topic), "formats": group(lambda p: p.media_type),
            "ab_tests": sorted(ab_tests, key=lambda x: x["published_at"], reverse=True)[:10],
            "top": [card(p) for p in ranked[:5]], "weakest": [card(p) for p in ranked[-5:][::-1] if len(ranked) > 5]}


ADVICE_SYSTEM = """You are the social media strategist of Quantum Foundation (a non-profit in Bangladesh: Quantum Method
meditation courses, programs, blood donation, Quantum Lab ...). You get the performance numbers of its recent posts
(engagement = reactions + comments + shares + saves; times are Bangladesh time) and its upcoming programs.
Give practical advice for the next 2 weeks: what to post, on which days and at what time, in which format, and why
(always point to the numbers). Plan posts for the upcoming programs (invite early, remind the day before). If there
are too few posts to be sure, say so and suggest what to test. Write in the language given by LANGUAGE (Bangla in
Bengali script, or English). Return ONLY JSON:
{"summary": "3-4 sentences", "plan": [{"when": "e.g. Fri 20:00", "platform": "facebook|instagram|both", "format": "photo|video|reel|text|link|carousel",
  "topic": "...", "idea": "one-sentence post idea", "why": "short reason from the numbers"}], "tests": ["an experiment to try", ...]}"""


async def advice(session: AsyncSession, llm: LLMService, *, language: str = "bn", days: int = 90) -> dict:
    a = await analytics(session, days=days)
    progs = [context_line(p, today_bd()) for p in (await upcoming(session))[:10]]
    stats = {k: a.get(k) for k in ("posts", "days", "avg_engagement", "avg_reach", "best_slots", "by_day", "topics", "formats")}
    stats["top"] = [{k: x[k] for k in ("message", "topic", "media_type", "engagement", "published_at")} for x in a.get("top", [])]
    content = (f"LANGUAGE: {'English' if language == 'en' else 'Bangla'}\nTODAY: {today_bd().isoformat()}\n"
               f"NUMBERS: {json.dumps(stats, ensure_ascii=False, default=str)[:6000]}\nUPCOMING PROGRAMS:\n" + ("\n".join(progs) or "(none)"))
    try:
        out = await llm.run(session, "answer", lambda pol: PrivacyGuard(pol).prepare_task(ADVICE_SYSTEM, content), channel=None, is_test=True)
    except LLMUnavailable as exc:
        return {"error": f"No AI model is available: {exc}", "stats": stats}
    d = parse_json_object(out.request.redactor.restore(out.result.text)) or {}
    return {"summary": d.get("summary"), "plan": [x for x in d.get("plan") or [] if isinstance(x, dict)][:12],
            "tests": [t for t in d.get("tests") or [] if isinstance(t, str)][:5], "posts_analysed": a.get("posts", 0)}


FORMATS = {
    "post": "a normal post (caption)",
    "reel": "a 30-60 second Reel: in \"text\" give the caption; in \"script\" the hook (first 3 seconds), 4-6 scenes "
            "(what is shown + what is said or written on screen) and the closing call to action",
    "story": "a Story of 3-5 frames: in \"text\" a one-line summary; in \"script\" each frame's text and picture idea, "
             "with a sticker idea (poll, question, link)",
}
DRAFT_SYSTEM = """You write social media posts for Quantum Foundation (a non-profit in Bangladesh: Quantum Method
meditation courses, programs, blood donation, Quantum Lab ...). Warm, respectful, inspiring; no exaggeration, no
invented facts: dates, places, fees and links only as given in FACTS. Write {count} different versions for the BRIEF,
for PLATFORM (Instagram: shorter, more line breaks, 5-8 hashtags at the end; Facebook: can be longer, 2-4 hashtags,
a clear call to action). Language: {language} (Bangla in Bengali script). Learn from TOP POSTS what the audience likes.
FORMAT: {format}.
Return ONLY JSON: {{"versions": [{{"text": "...", "script": "only for a reel or story, else empty", "best_time": "e.g. Fri 20:00 (from the numbers, if any)", "note": "why this works"}}]}}"""


async def draft(session: AsyncSession, llm: LLMService, *, brief: str, platform: str = "facebook", language: str = "bn",
                program_id: int | None = None, count: int = 3, format: str = "post") -> dict:
    facts = []
    for p in await upcoming(session):
        if program_id is None or p.id == program_id:
            facts.append(context_line(p, today_bd()))
    a = await analytics(session, days=120)
    top = [x["message"] for x in a.get("top", [])][:4]
    slots = [x["label"] for x in a.get("best_slots", [])][:3]
    content = (f"BRIEF: {brief[:1500]}\nPLATFORM: {platform}\nFACTS:\n" + ("\n".join(facts[: 1 if program_id else 8]) or "(none)")
               + "\nTOP POSTS:\n" + ("\n".join(f"- {t}" for t in top) or "(none yet)")
               + f"\nBEST TIMES SO FAR: {', '.join(slots) or 'unknown'}")
    system = DRAFT_SYSTEM.format(count=max(1, min(count, 5)), language="English" if language == "en" else "Bangla",
                                 format=FORMATS.get(format, FORMATS["post"]))
    try:
        out = await llm.run(session, "answer", lambda pol: PrivacyGuard(pol).prepare_task(system, content), channel=None, is_test=True)
    except LLMUnavailable as exc:
        return {"error": f"No AI model is available: {exc}", "versions": []}
    d = parse_json_object(out.request.redactor.restore(out.result.text)) or {}
    return {"versions": [v for v in d.get("versions") or [] if isinstance(v, dict) and str(v.get("text", "")).strip()][:5]}


# ---- drafts the AI prepares (a person approves) --------------------------------------------------------------------
INVITE = ("Invite people to the program {name}: what it is, when and where (from FACTS), who it is for, and how to join "
          "(the registration link if there is one).")
REMIND = "A short, warm reminder that the program {name} is tomorrow: when, where, and a welcome."


async def _facebook_pages(session: AsyncSession) -> list[dict]:
    return [a for a in await accounts(session) if a["channel"] == "facebook" and a["has_token"]]


async def program_drafts(session: AsyncSession, llm: LLMService | None, *, within_days: int = 30) -> int:
    """For every upcoming program (within `within_days`) that has no post yet: an invitation draft and a reminder draft
    for the day before (18:00 Bangladesh time), on each connected Facebook Page."""
    from app.services.settings_service import get_setting
    if not await get_setting(session, "auto_program_drafts"):
        return 0
    pages = await _facebook_pages(session)
    if not pages:
        return 0
    today = today_bd()
    done = set((await session.execute(select(SocialPost.program_id).where(SocialPost.program_id.is_not(None)))).scalars())
    best = ((await analytics(session, days=120)).get("best_slots") or [{}])[0].get("label", "")
    made = 0
    for prog in await upcoming(session, today):
        if prog.id in done or (prog.start_date - today).days > within_days or prog.start_date <= today:
            continue
        eve = datetime.combine(prog.start_date - timedelta(days=1), datetime.min.time()).replace(hour=18, tzinfo=BD_TZ)
        for kind, brief, when_ in (("invitation", INVITE, None), ("reminder", REMIND, eve.astimezone(timezone.utc))):
            text = None
            if llm is not None:
                got = await draft(session, llm, brief=brief.format(name=prog.name), program_id=prog.id, count=1)
                text = (got.get("versions") or [{}])[0].get("text")
            text = text or (f"{prog.name}\n{context_line(prog, today).split(' | ', 1)[-1]}" if kind == "invitation"
                            else f"আগামীকাল {prog.name}। আপনাকে আন্তরিক আমন্ত্রণ।")
            for page in pages:
                p = SocialPost(channel="facebook", account_id=page["account_id"], source="app", status="draft", message=text,
                               link=prog.registration_url, program_id=prog.id, ai_written=llm is not None,
                               suggested_at=when_ or next_slot(best) or datetime.now(timezone.utc) + timedelta(days=1),
                               note=f"{kind.capitalize()} for {prog.name} (made automatically)")
                log(p, "AI", f"{kind} draft for program {prog.id}")
                session.add(p)
                made += 1
        done.add(prog.id)
    return made


async def auto_plan(session: AsyncSession, llm: LLMService, *, language: str = "bn") -> dict:
    """The AI's 2-week plan turned into drafts with the suggested time and the reason. Nothing is published."""
    plan = await advice(session, llm, language=language)
    if plan.get("error"):
        return {"error": plan["error"], "drafts": 0}
    pages = await _facebook_pages(session)
    igs = [a for a in await accounts(session) if a["channel"] == "instagram" and a["has_token"]]
    made = 0
    for item in (plan.get("plan") or [])[:8]:
        where = (item.get("platform") or "facebook").lower()
        targets = (pages if where in ("facebook", "both") else []) + (igs if where in ("instagram", "both") else [])
        if not targets:
            continue
        fmt = "reel" if "reel" in (item.get("format") or "") else "story" if "story" in (item.get("format") or "") else "post"
        got = await draft(session, llm, brief=item.get("idea") or item.get("topic") or "", platform=targets[0]["channel"],
                          language=language, count=1, format=fmt)
        v = (got.get("versions") or [{}])[0]
        if not v.get("text"):
            continue
        for t in targets:
            p = SocialPost(channel=t["channel"], account_id=t["account_id"], source="app", status="draft", message=v["text"],
                           ai_written=True, suggested_at=next_slot(item.get("when") or ""), topic=(item.get("topic") or None),
                           media_type=None if fmt == "post" else fmt,
                           note="; ".join(x for x in (item.get("why"), v.get("script") and f"Script: {v['script']}",
                                                      t["channel"] == "instagram" and "Add an image before publishing") if x)[:4000])
            log(p, "AI", "draft from the AI's plan")
            session.add(p)
            made += 1
    return {"drafts": made, "summary": plan.get("summary")}


def new_ab_group() -> str:
    return uuid.uuid4().hex[:12]
