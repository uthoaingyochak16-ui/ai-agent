"""Social posts: write, schedule and publish to Facebook / Instagram; read posts back with numbers; the analysis."""
import io
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select

import app.llm.service as svc
from app.core import crypto
from app.models import ChannelConfig, SocialPost
from app.services.posts import analytics, publish_due
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login

G = "https://graph.facebook.com/v21.0"


async def connect(session):
    session.add_all([ChannelConfig(channel="messenger", enabled=True, account_id="PAGE1", status="ok", label="Quantum Page",
                                   access_token_enc=crypto.encrypt("T", aad="token:messenger")),
                     ChannelConfig(channel="instagram", enabled=True, account_id="IG1", status="ok", label="Quantum IG",
                                   access_token_enc=crypto.encrypt("T", aad="token:instagram"))])
    await session.commit()


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(svc, "default_factory", f)
    return f


@respx.mock
async def test_publish_now_to_a_page_and_instagram(client, maker, session):
    await connect(session)
    staff = await login(client, maker, "agent", "agent@x.com")
    feed = respx.post(f"{G}/PAGE1/photos").mock(return_value=httpx.Response(200, json={"id": "PH1", "post_id": "PAGE1_P1"}))
    respx.get(f"{G}/PAGE1_P1").mock(return_value=httpx.Response(200, json={"permalink_url": "https://fb.com/p1"}))
    container = respx.post(f"{G}/IG1/media").mock(return_value=httpx.Response(200, json={"id": "C1"}))
    pub = respx.post(f"{G}/IG1/media_publish").mock(return_value=httpx.Response(200, json={"id": "M1"}))
    respx.get(f"{G}/M1").mock(return_value=httpx.Response(200, json={"permalink": "https://ig.com/p/m1"}))
    r = await client.post("/api/posts", json={"targets": [{"channel": "facebook", "account_id": "PAGE1"}, {"channel": "instagram", "account_id": "IG1"}],
                                              "message": "সঙ্ঘায়ন ২১ অক্টোবর", "image_url": "https://x.org/a.jpg", "publish_now": True}, headers=staff)
    assert r.status_code == 200, r.text
    fb, ig = r.json()
    assert (fb["status"], fb["permalink"], fb["account_label"]) == ("published", "https://fb.com/p1", "Quantum Page")
    assert (ig["status"], ig["permalink"]) == ("published", "https://ig.com/p/m1")
    assert json.loads(feed.calls[0].request.content)["caption"] == "সঙ্ঘায়ন ২১ অক্টোবর"
    assert json.loads(pub.calls[0].request.content) == {"creation_id": "C1"} and container.called
    bad = await client.post("/api/posts", json={"targets": [{"channel": "instagram", "account_id": "IG1"}], "message": "text only"}, headers=staff)
    assert bad.status_code == 422 and "image" in bad.text


@respx.mock
async def test_a_scheduled_post_is_published_once_when_its_time_comes(client, maker, session):
    await connect(session)
    staff = await login(client, maker, "agent", "agent@x.com")
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    [p] = (await client.post("/api/posts", json={"targets": [{"channel": "facebook", "account_id": "PAGE1"}], "message": "Hello",
                                                 "schedule_at": soon}, headers=staff)).json()
    assert p["status"] == "scheduled"
    feed = respx.post(f"{G}/PAGE1/feed").mock(return_value=httpx.Response(200, json={"id": "PAGE1_P9"}))
    respx.get(f"{G}/PAGE1_P9").mock(return_value=httpx.Response(200, json={}))
    assert await publish_due(maker) == 0                                        # not yet
    async with maker() as s:
        (await s.get(SocialPost, p["id"])).scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
    assert await publish_due(maker) == 1 and await publish_due(maker) == 0      # once
    async with maker() as s:
        row = await s.get(SocialPost, p["id"])
    assert (row.status, row.external_id, feed.call_count) == ("published", "PAGE1_P9", 1)


@respx.mock
async def test_sync_reads_posts_with_numbers_tags_topics_and_the_analysis_finds_the_best_time(client, maker, session, fake):
    await connect(session)
    await seed_llm(session, routes={"router": ("anthropic", "cheap", None, None), "answer": ("anthropic", "big", None, None)})
    fri8 = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)                   # Friday 20:00 in Dhaka
    mon10 = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)                   # Monday 10:00
    def fb_post(i, when, likes):
        return {"id": f"PAGE1_{i}", "message": f"post {i}", "created_time": when.isoformat(), "permalink_url": f"https://fb.com/{i}",
                "status_type": "added_photos", "reactions": {"summary": {"total_count": likes}}, "comments": {"summary": {"total_count": 2}},
                "shares": {"count": 1}}
    respx.get(f"{G}/PAGE1/posts").mock(return_value=httpx.Response(200, json={"data": [
        fb_post(1, fri8, 90), fb_post(2, fri8 - timedelta(days=7), 70), fb_post(3, mon10, 5), fb_post(4, mon10 - timedelta(days=7), 7)]}))
    respx.get(url__regex=rf"{G}/PAGE1_\d/insights").mock(return_value=httpx.Response(200, json={"data": [
        {"name": "post_impressions_unique", "values": [{"value": 1000}]}]}))
    respx.get(f"{G}/IG1/media").mock(return_value=httpx.Response(400, json={"error": {"code": 10, "message": "no permission"}}))
    import re

    def label(req):  # "N. post K": posts 1 and 2 are quotes, 3 and 4 reminders
        lines = re.findall(r"(\d+)\. post (\d)", req.messages[0]["content"])
        return json.dumps({n: "Quote" if k in "12" else "Event Reminder" for n, k in lines})
    fake.script["cheap"] = label
    staff = await login(client, maker, "agent", "agent@x.com")
    r = (await client.post("/api/posts/sync", headers=staff)).json()
    assert {(x["channel"], x.get("posts"), x.get("error")) for x in r["accounts"]} == {("facebook", 4, None), ("instagram", None, "no permission")}
    assert r["tagged"] == 4
    async with maker() as s:
        a = await analytics(s, days=3650)
    assert a["posts"] == 4 and a["avg_reach"] == 1000
    assert a["best_slots"][0]["label"] == "Fri 20:00" and a["topics"][0]["name"] == "Quote"
    assert a["top"][0]["engagement"] == 93 and a["top"][0]["rate"] == 0.093
    again = (await client.post("/api/posts/sync", headers=staff)).json()        # no duplicates
    assert [x.get("new") for x in again["accounts"] if x["channel"] == "facebook"] == [0]


async def test_ai_writer_and_advice_return_their_json(client, maker, session, fake):
    await seed_llm(session, routes={"answer": ("anthropic", "big", None, None)})
    staff = await login(client, maker, "agent", "agent@x.com")
    fake.script["big"] = json.dumps({"versions": [{"text": "আসুন সঙ্ঘায়নে!", "best_time": "Fri 20:00", "note": "invite"}]})
    d = (await client.post("/api/posts/draft", json={"brief": "Invite to Songhayon"}, headers=staff)).json()
    assert d["versions"][0]["text"] == "আসুন সঙ্ঘায়নে!"
    fake.script["big"] = json.dumps({"summary": "Post on Fridays.", "plan": [{"when": "Fri 20:00", "topic": "Quote"}], "tests": ["Try reels"]})
    a = (await client.post("/api/posts/advice", json={"language": "en"}, headers=staff)).json()
    assert a["summary"] == "Post on Fridays." and a["plan"][0]["when"] == "Fri 20:00" and a["tests"] == ["Try reels"]


async def test_uploaded_images_are_served_on_a_public_link(client, maker, monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.core.config import get_settings
    get_settings.cache_clear()
    staff = await login(client, maker, "agent", "agent@x.com")
    r = (await client.post("/api/posts/upload", files={"file": ("a.png", io.BytesIO(b"\x89PNG data"), "image/png")}, headers=staff)).json()
    name = r["url"].rsplit("/", 1)[-1]
    got = await client.get(f"/api/media/{name}")                                # no login: Meta fetches it
    assert got.status_code == 200 and got.content == b"\x89PNG data"
    assert (await client.get("/api/media/../secret")).status_code == 404
    bad = await client.post("/api/posts/upload", files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")}, headers=staff)
    assert bad.status_code == 422
    get_settings.cache_clear()
