"""The social suite: approval, A/B tests, program and AI-plan drafts, audience, sentiment, questions, funnel, weekly
report, WhatsApp broadcast, ad drafts."""
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import func, select

import app.llm.service as svc
from app.core import crypto
from app.models import (AccountStat, AdDraft, Broadcast, ChannelConfig, ChannelIdentity, Conversation, Lead, Message,
                        PostComment, Profile, Program, SocialPost)
from app.rag.programs import BD_TZ, today_bd
from app.services import posts as P
from app.services import social as S
from app.services.settings_service import set_setting
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login
from tests.test_posts import G, connect


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(svc, "default_factory", f)
    return f


def test_next_slot_is_the_next_such_moment_in_dhaka():
    mon = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)                 # Monday 12:00 Dhaka
    t = P.next_slot("Fri 20:00", mon).astimezone(BD_TZ)
    assert (t.weekday(), t.hour, t.minute, t.date()) == (4, 20, 0, date(2026, 10, 23))
    assert P.next_slot("Mon 11:00", mon).astimezone(BD_TZ).date() == date(2026, 10, 26)     # passed today: next week
    assert P.next_slot("soon") is None


async def test_approval_ab_versions_and_history(client, maker, session):
    await connect(session)
    await set_setting(session, "post_approval_required", True)
    await session.commit()
    agent = await login(client, maker, "agent", "agent@x.com")
    admin = await login(client, maker, "admin", "boss@x.com")
    at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    posts = (await client.post("/api/posts", json={"targets": [{"channel": "facebook", "account_id": "PAGE1"}], "message": "Version A",
                                                  "variants": ["Version B"], "schedule_at": at}, headers=agent)).json()
    assert [(p["ab_variant"], p["status"]) for p in posts] == [("A", "pending_approval"), ("B", "pending_approval")]
    assert posts[0]["ab_group"] == posts[1]["ab_group"]
    assert (await client.post(f"/api/posts/{posts[0]['id']}/approve", json={}, headers=agent)).status_code == 403
    ok = (await client.post(f"/api/posts/{posts[0]['id']}/approve", json={"note": "fine"}, headers=admin)).json()
    back = (await client.post(f"/api/posts/{posts[1]['id']}/reject", json={"note": "shorter please"}, headers=admin)).json()
    assert ok["status"] == "scheduled" and [h["action"] for h in ok["history"]][-1].startswith("approved")
    assert (back["status"], back["note"]) == ("draft", "shorter please")
    # the analysis compares the versions of a test
    async with maker() as s:
        for pid, likes in ((posts[0]["id"], 10), (posts[1]["id"], 40)):
            p = await s.get(SocialPost, pid)
            p.status, p.published_at, p.metrics = "published", datetime.now(timezone.utc), {"reactions": likes, "reach": 100}
        await s.commit()
        a = await P.analytics(s)
    assert a["ab_tests"][0]["winner"] == "B"


async def test_program_drafts_once_per_program_and_the_ai_plan_makes_drafts(maker, session, fake):
    await connect(session)
    await seed_llm(session, routes={"answer": ("anthropic", "big", None, None)})
    session.add(Program(name="Songhayon", start_date=today_bd() + timedelta(days=5), place="IDEB", registration_url="https://f.org/r"))
    await session.commit()
    fake.script["big"] = json.dumps({"versions": [{"text": "আসুন!"}]})
    async with maker() as s:
        n1 = await P.program_drafts(s, P.LLMService(maker))
        n2 = await P.program_drafts(s, P.LLMService(maker))
        await s.commit()
        drafts = (await s.execute(select(SocialPost).order_by(SocialPost.id))).scalars().all()
    assert (n1, n2) == (2, 0)
    assert [d.status for d in drafts] == ["draft", "draft"] and drafts[1].suggested_at.astimezone(BD_TZ).hour == 18
    assert drafts[0].link == "https://f.org/r" and "Invitation" in drafts[0].note
    plans = iter([json.dumps({"summary": "s", "plan": [{"when": "Fri 20:00", "platform": "both", "format": "photo", "topic": "Quote", "idea": "a quote", "why": "Fridays win"}]}),
                  json.dumps({"versions": [{"text": "উক্তি"}]})])
    fake.script["big"] = lambda req: next(plans)
    async with maker() as s:
        res = await P.auto_plan(s, P.LLMService(maker))
        await s.commit()
        made = (await s.execute(select(SocialPost).where(SocialPost.message == "উক্তি"))).scalars().all()
    assert res["drafts"] == 2 and {p.channel for p in made} == {"facebook", "instagram"}
    assert all(p.suggested_at.astimezone(BD_TZ).weekday() == 4 for p in made)
    assert "Add an image" in next(p.note for p in made if p.channel == "instagram")


@respx.mock
async def test_audience_snapshot_keeps_one_row_a_day(maker, session):
    await connect(session)
    respx.get(f"{G}/PAGE1").mock(return_value=httpx.Response(200, json={"followers_count": 1200}))
    respx.get(f"{G}/PAGE1/insights").mock(return_value=httpx.Response(200, json={"data": [{"name": "page_impressions_unique", "values": [{"value": 5}, {"value": 900}]}]}))
    respx.get(f"{G}/IG1").mock(return_value=httpx.Response(200, json={"followers_count": 300}))
    respx.get(f"{G}/IG1/insights").mock(return_value=httpx.Response(400, json={"error": {"code": 10, "message": "no"}}))
    async with maker() as s:
        await S.snapshot(s)
        await S.snapshot(s)
        await s.commit()
        assert (await s.execute(select(func.count()).select_from(AccountStat))).scalar_one() == 2
        aud = {a["channel"]: a for a in await S.audience(s)}
    assert aud["facebook"]["followers"] == 1200 and aud["facebook"]["trend"][0]["reach"] == 900
    assert aud["instagram"]["followers"] == 300


async def test_sentiment_spike_and_funnel(maker, session):
    now = datetime.now(timezone.utc)
    for i, kind in enumerate(["praise", "praise", "complaint", "against", "abuse", "question"]):
        session.add(PostComment(page_id="P", post_id="P_1", comment_id=f"c{i}", message="Songhayon kobe?" if kind == "question" else "x",
                                kind=kind, received_at=now - timedelta(hours=2)))
    prog = Program(name="Songhayon", start_date=today_bd() + timedelta(days=3), registration_url="https://f.org/r")
    session.add(prog)
    p = Profile(name="R")
    session.add(p)
    await session.flush()
    ident = ChannelIdentity(channel="messenger", external_id="u1", profile_id=p.id)
    session.add(ident)
    await session.flush()
    conv = Conversation(channel_identity_id=ident.id, profile_id=p.id)
    session.add(conv)
    await session.flush()
    session.add_all([Message(conversation_id=conv.id, role="user", content_redacted="Songhayon e ki registration lage?"),
                     Message(conversation_id=conv.id, role="assistant", content_redacted="ফর্ম: https://f.org/r"),
                     Lead(profile_id=p.id, interest="Songhayon", program_id=prog.id),
                     SocialPost(channel="facebook", account_id="P", status="published", message="Songhayon e asun", published_at=now,
                                metrics={"reactions": 20, "comments": 5})])
    await session.commit()
    async with maker() as s:
        sen = await S.sentiment(s)
        spike = await S.spike_alert(s)
        f = {x["name"]: x for x in await S.funnel(s)}
    assert sen["totals"]["positive"] == 2 and sen["totals"]["negative"] == 3 and sen["mood"] == -0.2
    assert spike and "3 negative" in spike
    assert {k: f["Songhayon"][k] for k in ("posts", "engagement", "asked", "link_given", "leads")} == {"posts": 1, "engagement": 25, "asked": 1, "link_given": 1, "leads": 1}


async def test_question_trends_and_weekly_report(client, maker, session, fake):
    await seed_llm(session, routes={"router": ("anthropic", "cheap", None, None), "answer": ("anthropic", "big", None, None)})
    session.add(PostComment(page_id="P", post_id="P_1", comment_id="c1", message="course fee koto?", kind="question"))
    await session.commit()
    fake.script["cheap"] = json.dumps({"topics": [{"topic": "Course fee", "count": 1, "examples": ["course fee koto?"], "post_idea": "Fee card"}]})
    fake.script["big"] = "এই সপ্তাহে ভালো গেছে।"
    staff = await login(client, maker, "agent", "agent@x.com")
    q = (await client.post("/api/social/questions/refresh", headers=staff)).json()
    assert q["topics"][0]["topic"] == "Course fee" and (await client.get("/api/social/questions", headers=staff)).json()["messages"] == 1
    r = (await client.post("/api/social/reports", headers=staff)).json()
    assert r["summary"] == "এই সপ্তাহে ভালো গেছে।" and r["data"]["top_questions"] == ["Course fee"]
    assert len((await client.get("/api/social/reports", headers=staff)).json()) == 1


@respx.mock
async def test_whatsapp_broadcast_goes_only_to_consenting_whatsapp_guests(client, maker, session):
    from app.services.broadcast import run
    session.add(ChannelConfig(channel="whatsapp", enabled=True, account_id="PN1", status="ok", access_token_enc=crypto.encrypt("T", aad="token:whatsapp")))
    for i, (consent, channel) in enumerate([(True, "whatsapp"), (False, "whatsapp"), (True, "messenger")]):
        p = Profile(name=f"g{i}", consent_given=consent)
        session.add(p)
        await session.flush()
        ident = ChannelIdentity(channel=channel, external_id=f"88017000000{i}", account_id="PN1", profile_id=p.id)
        session.add(ident)
        await session.flush()
        session.add(Conversation(channel_identity_id=ident.id, profile_id=p.id))
    await session.commit()
    admin = await login(client, maker)
    assert (await client.post("/api/social/broadcasts/audience", json={}, headers=admin)).json() == {"count": 1}
    b = (await client.post("/api/social/broadcasts", json={"name": "Songhayon news", "template": "program_news", "params": ["Songhayon", "21 Oct"],
                                                           "preview": "Songhayon 21 Oct"}, headers=admin)).json()
    sent = respx.post(f"{G}/PN1/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}))
    started = (await client.post(f"/api/social/broadcasts/{b['id']}/start", headers=admin)).json()
    assert (started["status"], started["total"]) == ("sending", 1)
    assert client.app.state.queue.jobs[-1][0] == "broadcast_job"
    res = await run(maker, b["id"])
    assert res == {"status": "done", "sent": 1, "failed": 0}
    body = json.loads(sent.calls[0].request.content)
    assert body["type"] == "template" and body["template"]["name"] == "program_news"
    agent = await login(client, maker, "agent", "agent@x.com")
    assert (await client.post("/api/social/broadcasts", json={"name": "x", "template": "t"}, headers=agent)).status_code == 403


async def test_ad_drafts_never_spend_and_results_need_an_account(client, maker, session, fake):
    await seed_llm(session, routes={"answer": ("anthropic", "big", None, None)})
    fake.script["big"] = json.dumps({"name": "Songhayon awareness", "objective": "awareness", "daily_budget": 500, "days": 5, "text": "আসুন",
                                     "audience": {"age_min": 20, "age_max": 55, "locations": ["Dhaka"], "bogus": 1}, "note": "Dhaka first"})
    staff = await login(client, maker, "agent", "agent@x.com")
    a = (await client.post("/api/social/ads/suggest", json={"goal": "More people at Songhayon"}, headers=staff)).json()
    assert (a["status"], a["objective"], a["daily_budget"], a["audience"]) == ("draft", "awareness", 500.0, {"age_min": 20, "age_max": 55, "locations": ["Dhaka"]})
    upd = (await client.put(f"/api/social/ads/{a['id']}", json={"status": "approved", "daily_budget": 300}, headers=staff)).json()
    assert (upd["status"], upd["daily_budget"]) == ("approved", 300.0)
    r = (await client.get("/api/social/ads/results", headers=staff)).json()
    assert r["configured"] is False
    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(AdDraft))).scalar_one() == 1
