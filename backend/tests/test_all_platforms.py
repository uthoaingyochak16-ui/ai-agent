"""Instagram comments next to Facebook ones, platform status in the inbox, editable unknown-question suggestions,
and sheet rows in the keyword table."""
import json

import httpx
import respx

from app.channels.page_comments import parse_ig_comments
from app.core import crypto
from app.llm.service import LLMService
from app.models import ChannelConfig, PostComment, UnansweredQuestion
from app.services.comments import process_comment
from app.services.settings_service import set_setting
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login
from tests.test_comments import ROUTES, classify
from tests.test_webhooks import post

G = "https://graph.facebook.com/v21.0"


def ig_payload(cid="IGC1", text="price koto?", sender="IGU1", account="IG1"):
    return {"object": "instagram", "entry": [{"id": account, "time": 1, "changes": [{"field": "comments", "value": {
        "from": {"id": sender, "username": "rahim_bd"}, "media": {"id": "MEDIA1", "media_product_type": "FEED"},
        "id": cid, "text": text}}]}]}


def test_instagram_comments_are_parsed_and_the_accounts_own_are_skipped():
    [e] = parse_ig_comments(ig_payload())
    assert (e.channel, e.page_id, e.post_id, e.comment_id, e.from_name) == ("instagram", "IG1", "MEDIA1", "IGC1", "rahim_bd")
    assert parse_ig_comments(ig_payload(sender="IG1")) == []
    assert parse_ig_comments({"object": "page", "entry": []}) == []


@respx.mock
async def test_an_instagram_comment_is_answered_through_instagrams_endpoints(client, maker, session):
    session.add(ChannelConfig(channel="instagram", enabled=True, account_id="IG1", status="ok", label="Quantum IG",
                              access_token_enc=crypto.encrypt("TOKEN", aad="token:instagram")))
    await session.commit()
    await seed_llm(session, routes=ROUTES)
    await set_setting(session, "comment_mode", "auto")
    await session.commit()
    assert (await post(client, "instagram", ig_payload())).json()["accepted"] == 1
    async with maker() as s:
        row = (await s.execute(PostComment.__table__.select())).first()
    media = respx.get(f"{G}/MEDIA1").mock(return_value=httpx.Response(200, json={"caption": "Songhayon 21 Oct", "permalink": "https://instagram.com/p/X"}))
    reply = respx.post(f"{G}/IGC1/replies").mock(return_value=httpx.Response(200, json={"id": "IGR1"}))
    status = await process_comment(maker, row.id, llm=LLMService(maker, factory=FakeLLM({"cheap": classify("question", "price koto?")})))
    async with maker() as s:
        c = await s.get(PostComment, row.id)
    assert status == "replied" and c.reply_comment_id == "IGR1" and not c.liked      # no likes on Instagram
    assert media.called and c.post_url == "https://instagram.com/p/X" and c.post_text == "Songhayon 21 Oct"
    assert json.loads(reply.calls[0].request.content)["message"] == c.reply_text
    staff = await login(client, maker, "agent", "agent@x.com")
    listed = (await client.get("/api/comments", params={"channel": "instagram"}, headers=staff)).json()
    assert [(x["channel"], x["account_label"], x["post_url"]) for x in listed["items"]] == [("instagram", "Quantum IG", "https://instagram.com/p/X")]
    assert listed["groups"]["done"] == 1
    hide = respx.post(f"{G}/IGC1", params={"hide": "true"}).mock(return_value=httpx.Response(200, json={"success": True}))
    assert (await client.post(f"/api/comments/{row.id}/hide", headers=staff)).json()["status"] == "hidden" and hide.called


async def test_inbox_summary_tells_which_platforms_are_connected_and_working(client, maker, session):
    session.add(ChannelConfig(channel="messenger", account_id="P1", status="error", status_detail="token expired"))
    await session.commit()
    staff = await login(client, maker, "agent", "agent@x.com")
    p = (await client.get("/api/conversations/summary", headers=staff)).json()["platforms"]
    assert p["messenger"] == {"accounts": 1, "working": 0, "problem": "token expired"}
    assert p["instagram"]["accounts"] == 0 and p["whatsapp"]["accounts"] == 0


async def test_staff_edit_the_ai_suggestion_of_an_unknown_question(client, maker, session):
    session.add(UnansweredQuestion(question="Sylhet office kothay?", draft_answer="AI draft", status="new"))
    await session.commit()
    staff = await login(client, maker, "agent", "agent@x.com")
    [u] = (await client.get("/api/unanswered", headers=staff)).json()
    r = (await client.patch(f"/api/unanswered/{u['id']}", json={"draft_answer": "  Sylhet: Zindabazar  ", "suggested_category": "Contact & Address",
                                                                "suggested_keywords": ["sylhet", " office ", "sylhet"]}, headers=staff)).json()
    assert (r["draft_answer"], r["suggested_category"], r["suggested_keywords"], r["status"]) == ("Sylhet: Zindabazar", "Contact & Address", ["sylhet", "office"], "reviewing")
    assert (await client.patch(f"/api/unanswered/{u['id']}", json={"status": "ignored"}, headers=staff)).json()["status"] == "ignored"


async def test_sheet_rows_are_listed_in_the_keyword_table(client, maker):
    from tests.test_sheets import make_books
    admin = await login(client, maker)
    await make_books(client, admin)
    r = (await client.get("/api/keywords/records", params={"type": "sheetrow", "search": "mahajatak"}, headers=admin)).json()
    assert r["total"] == 2 and all(x["title"].startswith("Books: ") for x in r["items"])
    ref = r["items"][0]["ref"]
    assert (await client.put(f"/api/keywords/records/sheetrow/{ref}", json={"add": ["boi"]}, headers=admin)).status_code == 200
    again = (await client.get("/api/keywords/records", params={"type": "sheetrow", "search": "mahajatak"}, headers=admin)).json()
    assert any(k["term"] == "boi" for x in again["items"] if x["ref"] == ref for k in x["keywords"])
