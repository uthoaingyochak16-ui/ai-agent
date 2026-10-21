"""Social media analysis beyond single posts.

- audience: followers and reach of every connected Page / Instagram account, saved once a day (social_account_stats)
  for the trend; for Instagram also when followers are online and who they are (age, city, gender) as Meta allows.
- sentiment: week by week, how many comments were positive (praise, greetings), negative (complaints, attacks,
  abuse), questions or spam, and how many chats were complaints; spike_alert raises an alert when negative ones jump.
- questions: what guests ask most (messages + comments + unknown questions), grouped by the AI, with a post idea each.
- funnel: per program, how many posts were about it (and their engagement), how many people asked about it, how often
  its registration link was given, and how many leads it brought.
- weekly_report: all of that for the last 7 days in a few sentences by the AI, saved and sent to the alert webhook.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.graph import GraphError, graph_request
from app.llm.service import LLMService, LLMUnavailable
from app.models import (AccountStat, ChannelIdentity, Conversation, Lead, Message, PostComment, Program, SocialPost,
                        SocialReport, UnansweredQuestion)
from app.privacy.guard import PrivacyGuard
from app.rag.programs import BD_TZ, today_bd, upcoming
from app.services import posts as P
from app.services.outbound import ChannelNotConfigured
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting, set_setting

logger = logging.getLogger(__name__)
POSITIVE, NEGATIVE = ("praise", "greeting"), ("complaint", "against", "abuse")


# ---- audience ----------------------------------------------------------------------------------------------------
async def _get(path: str, creds, **params) -> dict | None:
    try:
        return await graph_request("GET", path, token=creds.access_token, base_url=creds.base_url, version=creds.version, params=params)
    except GraphError as exc:
        logger.info("audience call %s refused: %s", path, exc.message)
        return None


def _last_value(d: dict | None, name: str):
    for m in (d or {}).get("data", []):
        if m.get("name") == name:
            vals = m.get("values") or []
            if vals:
                return vals[-1].get("value")
            return (m.get("total_value") or {}).get("value")
    return None


async def _fetch(acc: dict, creds) -> tuple[int | None, dict]:
    aid = acc["account_id"]
    if acc["channel"] == "instagram":
        me = await _get(aid, creds, fields="followers_count,media_count,username") or {}
        m: dict = {"media": me.get("media_count")}
        day = await _get(f"{aid}/insights", creds, metric="reach", period="day")
        m["reach_day"] = _last_value(day, "reach")
        online = await _get(f"{aid}/insights", creds, metric="online_followers", period="lifetime")
        hours = _last_value(online, "online_followers")
        if isinstance(hours, dict):
            m["online_hours"] = {int(h): v for h, v in hours.items() if isinstance(v, (int, float))}
        for b in ("age", "city", "gender"):
            d = await _get(f"{aid}/insights", creds, metric="follower_demographics", period="lifetime", metric_type="total_value", breakdown=b)
            res = (((d or {}).get("data") or [{}])[0].get("total_value") or {}).get("breakdowns") or []
            rows = (res[0].get("results") if res else None) or []
            if rows:
                m[f"by_{b}"] = sorted(({"name": ",".join(r.get("dimension_values") or []), "value": r.get("value", 0)} for r in rows),
                                      key=lambda x: -x["value"])[:10]
        return me.get("followers_count"), m
    me = await _get(aid, creds, fields="followers_count,fan_count,name") or {}
    ins = await _get(f"{aid}/insights", creds, metric="page_impressions_unique,page_post_engagements", period="day")
    return me.get("followers_count") or me.get("fan_count"), {"reach_day": _last_value(ins, "page_impressions_unique"),
                                                             "engagement_day": _last_value(ins, "page_post_engagements")}


async def snapshot(session: AsyncSession) -> list[dict]:
    """Today's numbers of every connected account (one row per account and day; a second run updates it)."""
    out = []
    day = today_bd()
    for acc in await P.accounts(session):
        if not acc["has_token"]:
            continue
        try:
            creds = await P._creds(session, acc["channel"], acc["account_id"])
        except ChannelNotConfigured:
            continue
        followers, metrics = await _fetch(acc, creds)
        metrics = {k: v for k, v in metrics.items() if v is not None}
        await session.execute(pg_insert(AccountStat).values(day=day, channel=acc["channel"], account_id=acc["account_id"],
                                                            followers=followers, metrics=metrics)
                              .on_conflict_do_update(constraint="uq_account_stat_day", set_={"followers": followers, "metrics": metrics}))
        out.append({"account": acc["label"], "channel": acc["channel"], "followers": followers})
    return out


async def audience(session: AsyncSession, days: int = 90) -> list[dict]:
    since = today_bd() - timedelta(days=days)
    rows = (await session.execute(select(AccountStat).where(AccountStat.day >= since).order_by(AccountStat.day))).scalars().all()
    by: dict[tuple[str, str], list[AccountStat]] = defaultdict(list)
    for r in rows:
        by[(r.channel, r.account_id)].append(r)
    out = []
    for acc in await P.accounts(session):
        rs = by.get((acc["channel"], acc["account_id"]), [])
        last = rs[-1] if rs else None
        first = next((r for r in rs if r.followers is not None), None)
        out.append({**acc, "followers": last.followers if last else None,
                    "growth": (last.followers - first.followers) if last and first and last.followers is not None and first.followers is not None else None,
                    "trend": [{"day": r.day, "followers": r.followers, "reach": (r.metrics or {}).get("reach_day")} for r in rs],
                    "online_hours": (last.metrics or {}).get("online_hours") if last else None,
                    "demographics": {k[3:]: v for k, v in ((last.metrics or {}) if last else {}).items() if k.startswith("by_")},
                    "updated": last.day if last else None})
    return out


# ---- sentiment ---------------------------------------------------------------------------------------------------
async def sentiment(session: AsyncSession, weeks: int = 12) -> dict:
    since = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    rows = (await session.execute(select(PostComment.received_at, PostComment.kind).where(
        PostComment.received_at >= since, PostComment.kind.is_not(None)))).all()
    chats = (await session.execute(select(Conversation.handover_reason, Message.created_at).join(Message, Message.conversation_id == Conversation.id)
                                   .where(Message.role == "user", Message.created_at >= since,
                                          Conversation.handover_reason.ilike("%complaint%")))).all()
    weeks_: dict[date, Counter] = defaultdict(Counter)

    def week(t: datetime) -> date:
        d = t.astimezone(BD_TZ).date()
        return d - timedelta(days=d.weekday())
    for t, kind in rows:
        group = "positive" if kind in POSITIVE else "negative" if kind in NEGATIVE else "question" if kind == "question" else "spam" if kind == "spam" else "other"
        weeks_[week(t)][group] += 1
    for _, t in chats:
        weeks_[week(t)]["complaint_chats"] += 1
    series = [{"week": w, **{k: c.get(k, 0) for k in ("positive", "negative", "question", "spam", "other", "complaint_chats")}}
              for w, c in sorted(weeks_.items())]
    tot = Counter()
    for s in series:
        tot.update({k: v for k, v in s.items() if k != "week"})
    mood = (tot["positive"] - tot["negative"]) / max(1, tot["positive"] + tot["negative"])
    return {"weeks": series, "totals": dict(tot), "mood": round(mood, 2)}


async def spike_alert(session: AsyncSession) -> str | None:
    """A text when negative comments of the last 24 h are at least 3 and twice the daily average of the 14 days before."""
    now = datetime.now(timezone.utc)
    neg = PostComment.kind.in_(NEGATIVE)
    last = (await session.execute(select(func.count()).select_from(PostComment).where(neg, PostComment.received_at >= now - timedelta(days=1)))).scalar_one()
    before = (await session.execute(select(func.count()).select_from(PostComment).where(
        neg, PostComment.received_at >= now - timedelta(days=15), PostComment.received_at < now - timedelta(days=1)))).scalar_one()
    if last >= max(3, 2 * before / 14):
        return f"{last} negative comment(s) (complaints, attacks, abuse) in the last 24 hours, against about {before / 14:.1f} a day before. Check Post comments."
    return None


# ---- what guests ask ---------------------------------------------------------------------------------------------
TRENDS_SYSTEM = """You read what guests asked a Bangladeshi non-profit (Quantum Foundation: meditation courses,
programs, blood donation, Quantum Lab ...) in chats and comments. Group them into the 6-12 most common topics.
For each give: a short topic name in English, how many of the messages belong to it (estimate), 2 typical examples
(as written), and one idea for a social media post that would answer it for everyone. Return ONLY JSON:
{"topics": [{"topic": "...", "count": 0, "examples": ["...", "..."], "post_idea": "..."}]}"""


async def question_trends(session: AsyncSession, llm: LLMService, days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    msgs = (await session.execute(select(Message.content_redacted).join(Conversation, Message.conversation_id == Conversation.id)
                                  .join(ChannelIdentity, Conversation.channel_identity_id == ChannelIdentity.id)
                                  .where(Message.role == "user", Message.created_at >= since, ~ChannelIdentity.external_id.like("test:%"),
                                         func.length(Message.content_redacted).between(8, 400))
                                  .order_by(Message.id.desc()).limit(250))).scalars().all()
    comments = (await session.execute(select(PostComment.message).where(PostComment.received_at >= since, PostComment.kind == "question")
                                      .limit(100))).scalars().all()
    unknown = (await session.execute(select(UnansweredQuestion.question).where(UnansweredQuestion.last_seen_at >= since).limit(50))).scalars().all()
    lines = [x for x in (*msgs, *comments, *unknown) if x and x.strip()]
    if not lines:
        res = {"at": datetime.now(timezone.utc).isoformat(), "days": days, "messages": 0, "topics": []}
    else:
        content = "\n".join(f"- {x[:300]}" for x in lines[:400])
        try:
            out = await llm.run(session, "router", lambda pol: PrivacyGuard(pol).prepare_task(TRENDS_SYSTEM, content), channel=None)
            d = parse_json_object(out.request.redactor.restore(out.result.text)) or {}
        except LLMUnavailable as exc:
            return {"error": f"No AI model is available: {exc}"}
        res = {"at": datetime.now(timezone.utc).isoformat(), "days": days, "messages": len(lines),
               "topics": [t for t in d.get("topics") or [] if isinstance(t, dict)][:12]}
    await set_setting(session, "question_trends", res)
    return res


# ---- program funnel ------------------------------------------------------------------------------------------------
async def funnel(session: AsyncSession, days: int = 60) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    progs = {p.id: p for p in await upcoming(session)}
    progs.update({p.id: p for p in (await session.execute(select(Program).where(Program.start_date >= (today_bd() - timedelta(days=days))))).scalars()})
    out = []
    for p in sorted(progs.values(), key=lambda x: x.start_date):
        name = p.name.strip()
        like = f"%{name[:60]}%"
        posts = (await session.execute(select(SocialPost).where(SocialPost.status == "published", SocialPost.published_at >= since,
                                                                (SocialPost.program_id == p.id) | SocialPost.message.ilike(like)))).scalars().all()
        asked = (await session.execute(select(func.count(func.distinct(Message.conversation_id))).where(
            Message.role == "user", Message.created_at >= since, Message.content_redacted.ilike(like)))).scalar_one()
        links = (await session.execute(select(func.count()).select_from(Message).where(
            Message.role.in_(("assistant", "agent")), Message.created_at >= since,
            Message.content_redacted.contains(p.registration_url)))).scalar_one() if p.registration_url else None
        leads = (await session.execute(select(func.count()).select_from(Lead).where(
            Lead.created_at >= since, (Lead.program_id == p.id) | Lead.interest.ilike(like)))).scalar_one()
        out.append({"id": p.id, "name": name, "start_date": p.start_date, "place": p.place, "posts": len(posts),
                    "engagement": sum(P.engagement(x.metrics) for x in posts), "asked": asked, "link_given": links, "leads": leads})
    return out


# ---- weekly report -------------------------------------------------------------------------------------------------
REPORT_SYSTEM = """Write the weekly social media report of Quantum Foundation for its team, from the NUMBERS (last 7
days, compared with the 7 days before). 5-8 short sentences in {language}: what went well, what went badly, the best
post, what guests asked most, and 2-3 concrete things to do next week. Plain text, no headings."""


async def weekly_report(session: AsyncSession, llm: LLMService | None) -> SocialReport:
    now = datetime.now(timezone.utc)
    end, start, prev = now, now - timedelta(days=7), now - timedelta(days=14)

    async def window(a: datetime, b: datetime) -> dict:
        posts = (await session.execute(select(SocialPost).where(SocialPost.status == "published", SocialPost.published_at >= a,
                                                                SocialPost.published_at < b))).scalars().all()
        kinds = dict((await session.execute(select(PostComment.kind, func.count()).where(PostComment.received_at >= a, PostComment.received_at < b)
                                            .group_by(PostComment.kind))).all())
        chats = (await session.execute(select(func.count(func.distinct(Message.conversation_id))).where(
            Message.role == "user", Message.created_at >= a, Message.created_at < b))).scalar_one()
        leads = (await session.execute(select(func.count()).select_from(Lead).where(Lead.created_at >= a, Lead.created_at < b))).scalar_one()
        unknown = (await session.execute(select(func.count()).select_from(UnansweredQuestion).where(
            UnansweredQuestion.created_at >= a, UnansweredQuestion.created_at < b))).scalar_one()
        best = max(posts, key=lambda p: P.engagement(p.metrics), default=None)
        return {"posts": len(posts), "engagement": sum(P.engagement(p.metrics) for p in posts),
                "best_post": {"text": best.message[:200], "engagement": P.engagement(best.metrics), "channel": best.channel} if best else None,
                "comments": {k or "unknown": v for k, v in kinds.items()}, "chats": chats, "leads": leads, "unanswered": unknown}
    data = {"this_week": await window(start, end), "last_week": await window(prev, start),
            "top_questions": [t.get("topic") for t in ((await get_setting(session, "question_trends")) or {}).get("topics", [])][:5]}
    lang = "English" if (await get_setting(session, "report_language") or "bn") == "en" else "Bangla (Bengali script)"
    summary = ""
    if llm is not None:
        try:
            out = await llm.run(session, "answer", lambda pol: PrivacyGuard(pol).prepare_task(
                REPORT_SYSTEM.format(language=lang), json.dumps(data, ensure_ascii=False, default=str)), channel=None, is_test=True)
            summary = out.request.redactor.restore(out.result.text).strip()
        except LLMUnavailable:
            summary = ""
    if not summary:
        t, l_ = data["this_week"], data["last_week"]
        summary = (f"Posts: {t['posts']} (last week {l_['posts']}), engagement {t['engagement']} ({l_['engagement']}). "
                   f"Chats: {t['chats']} ({l_['chats']}). Leads: {t['leads']} ({l_['leads']}). Unanswered questions: {t['unanswered']}.")
    rep = SocialReport(period_start=start.astimezone(BD_TZ).date(), period_end=end.astimezone(BD_TZ).date(), summary=summary, data=data)
    session.add(rep)
    await session.flush()
    from app.services.monitoring import Alert, send_alerts
    sent = await send_alerts(session, [Alert(f"weekly_report:{rep.period_end}", f"Weekly social media report ({rep.period_start} – {rep.period_end}):\n{summary}")])
    rep.sent = bool(sent)
    return rep
