"""Comments on the Facebook Page's posts: webhook -> row -> AI plan -> like / reply / hide, or a draft for staff."""
import json

import httpx
import respx
from sqlalchemy import select

from app.channels.page_comments import parse_feed
from app.llm.service import LLMService
from app.models import PostComment
from app.rag.embeddings import HashingEmbedder
from app.services.comments import process_comment
from app.services.settings_service import set_setting
from tests.conftest import configure_channel, seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login
from tests.test_webhooks import post

G = "https://graph.facebook.com/v21.0"
ROUTES = {"router": ("anthropic", "cheap", None, None), "answer": ("anthropic", "big", None, None),
          "answer_large": ("anthropic", "big", None, None)}


def feed(comment_id="POST1_C1", message="kobe hobe?", sender="U1", parent=None, verb="add", item="comment"):
    return {"object": "page", "entry": [{"id": "PAGE1", "time": 1, "changes": [{"field": "feed", "value": {
        "from": {"id": sender, "name": "Rahim"}, "item": item, "verb": verb, "post_id": "PAGE1_POST1",
        "comment_id": comment_id, "parent_id": parent or "PAGE1_POST1", "message": message, "created_time": 1}}]}]}


def classify(kind, question="", reply="", language="bn", confidence=0.95, reason="because"):
    return json.dumps({"kind": kind, "language": language, "question": question, "reply": reply,
                       "confidence": confidence, "reason": reason})


async def prepare(session, mode="auto", **settings):
    await configure_channel(session, account_id="PAGE1")
    await seed_llm(session, routes=ROUTES)
    await set_setting(session, "comment_mode", mode)
    for k, v in settings.items():
        await set_setting(session, k, v)
    await session.commit()


async def add(session, **kw) -> int:
    c = PostComment(**{"page_id": "PAGE1", "post_id": "PAGE1_POST1", "comment_id": "POST1_C1", "parent_id": "PAGE1_POST1",
                       "from_id": "U1", "message": "kobe hobe?", "post_text": "Songhayon on 21 October at IDEB Bhaban", **kw})
    session.add(c)
    await session.commit()
    return c.id


async def run(maker, row_id, script):
    fake = FakeLLM(script)
    status = await process_comment(maker, row_id, llm=LLMService(maker, factory=fake), embedder=(HashingEmbedder(1024), None))
    async with maker() as s:
        return status, await s.get(PostComment, row_id), fake


def test_parse_feed_keeps_new_comments_only():
    assert [e.comment_id for e in parse_feed(feed())] == ["POST1_C1"]
    assert parse_feed(feed(sender="PAGE1")) == []         # our own reply comes back through the webhook
    assert parse_feed(feed(verb="edited")) == []
    assert parse_feed(feed(item="reaction")) == []
    assert parse_feed({"object": "instagram", "entry": []}) == []


async def test_webhook_stores_and_enqueues_comments_only_when_switched_on(client, maker, session):
    await configure_channel(session, account_id="PAGE1")
    r = await post(client, "messenger", feed())
    assert r.status_code == 200 and r.json()["accepted"] == 0      # comment_mode "off" by default
    await set_setting(session, "comment_mode", "review")
    await session.commit()
    assert (await post(client, "messenger", feed())).json()["accepted"] == 1
    assert (await post(client, "messenger", feed())).json()["accepted"] == 0   # Meta's retry: stored once
    assert [j[0] for j in client.app.state.queue.jobs] == ["process_comment_job"]
    assert client.app.state.queue.options[0]["_defer_by"].total_seconds() == 20


@respx.mock
async def test_auto_mode_likes_and_answers_a_question_from_the_knowledge(maker, session):
    await prepare(session, general_answer_enabled=True)
    row = await add(session, message="meditation ki?")
    like = respx.post(f"{G}/POST1_C1/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    reply = respx.post(f"{G}/POST1_C1/comments").mock(return_value=httpx.Response(200, json={"id": "POST1_R1"}))
    status, c, fake = await run(maker, row, {
        "cheap": classify("question", "What is meditation?"),
        "big": "মেডিটেশন হলো মনকে শান্ত ও একাগ্র করার একটি চর্চা। বিস্তারিত জানতে \"agent\" লিখুন।"})
    assert status == "replied" and c.liked and c.reply_comment_id == "POST1_R1"
    assert c.reply_text == "মেডিটেশন হলো মনকে শান্ত ও একাগ্র করার একটি চর্চা।"     # never "type agent" in public
    assert like.called and json.loads(reply.calls[0].request.content) == {"message": c.reply_text}
    answer_req = next(req for m, req in fake.calls if m == "big")
    assert "PUBLIC COMMENT MODE" in answer_req.system and "Songhayon on 21 October" in answer_req.system


@respx.mock
async def test_question_the_knowledge_cannot_answer_gets_the_inbox_pointer(maker, session):
    await prepare(session)
    row = await add(session, message="Apnader Sylhet office kothay?")
    respx.post(f"{G}/POST1_C1/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    reply = respx.post(f"{G}/POST1_C1/comments").mock(return_value=httpx.Response(200, json={"id": "R"}))
    status, c, fake = await run(maker, row, {"cheap": classify("question", "Sylhet office kothay?")})
    assert status == "replied" and c.reply_path == "canned" and "মেসেজ" in c.reply_text
    assert "big" not in fake.models() and reply.called    # nothing in the knowledge: no guess, no answer call


@respx.mock
async def test_review_mode_drafts_the_reply_and_staff_send_it(client, maker, session):
    await prepare(session, mode="review")
    row = await add(session, message="Alhamdulillah, khub valo laglo")
    reply = respx.post(f"{G}/POST1_C1/comments").mock(return_value=httpx.Response(200, json={"id": "R9"}))
    respx.post(f"{G}/POST1_C1/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    status, c, _ = await run(maker, row, {"cheap": classify("praise", reply="আলহামদুলিল্লাহ, আপনাকে ধন্যবাদ! 🙏")})
    assert status == "draft" and c.reply_text.startswith("আলহামদুলিল্লাহ") and not reply.called

    staff = await login(client, maker, "agent", "agent@x.com")
    listed = (await client.get("/api/comments", params={"status": "draft"}, headers=staff)).json()
    assert [x["id"] for x in listed["items"]] == [row] and listed["settings"]["comment_mode"] == "review"
    sent = (await client.post(f"/api/comments/{row}/send", json={"text": "ধন্যবাদ! 🌸"}, headers=staff)).json()
    assert sent["status"] == "replied" and sent["reply_path"] == "staff" and sent["liked"]
    assert json.loads(reply.calls[0].request.content) == {"message": "ধন্যবাদ! 🌸"}
    assert (await client.post(f"/api/comments/{row}/send", json={}, headers=staff)).status_code == 409


@respx.mock
async def test_emoji_is_liked_spam_is_hidden_complaint_goes_to_staff(maker, session):
    await prepare(session, comment_spam_action="hide")
    respx.post(url__regex=rf"{G}/C\d/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    hide = respx.post(f"{G}/C2").mock(return_value=httpx.Response(200, json={"success": True}))
    respx.post(f"{G}/C3/comments").mock(return_value=httpx.Response(200, json={"id": "R3"}))
    emoji = await add(session, comment_id="C1", message="👍👍")
    spam = await add(session, comment_id="C2", message="Earn 5000 taka daily, click bit.ly/x")
    angry = await add(session, comment_id="C3", message="Apnara phone e kokhono uttor den na")
    s1, c1, f1 = await run(maker, emoji, {})
    s2, _, _ = await run(maker, spam, {"cheap": classify("spam")})
    s3, c3, _ = await run(maker, angry, {"cheap": classify("complaint")})
    assert (s1, c1.liked, c1.reply_text, f1.calls) == ("liked", True, None, [])   # no AI call for an emoji
    assert s2 == "hidden" and json.loads(hide.calls[0].request.content) == {"is_hidden": True}
    assert s3 == "needs_staff" and not c3.liked and "দুঃখিত" in c3.reply_text


@respx.mock
async def test_threads_only_the_same_person_after_our_reply(maker, session):
    await prepare(session)
    await add(session, comment_id="C1", from_id="U1", message="kobe hobe?", reply_text="২১ অক্টোবর।", status="replied",
              reply_comment_id="R1")
    other = await add(session, comment_id="C2", parent_id="C1", from_id="U2", message="ami o jabo")
    same = await add(session, comment_id="C3", parent_id="C1", from_id="U1", message="kothay?")
    respx.post(f"{G}/C3/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    respx.post(f"{G}/C3/comments").mock(return_value=httpx.Response(200, json={"id": "R3"}))
    s_other, c_other, f_other = await run(maker, other, {"cheap": classify("other")})
    s_same, _, f_same = await run(maker, same, {"cheap": classify("question", "Songhayon kothay hobe?")})
    assert (s_other, c_other.kind, c_other.reply_text) == ("ignored", "thread", None)   # only checked for spam / attacks
    assert f_other.models() == ["cheap"]
    assert s_same == "replied" and "PAGE: ২১ অক্টোবর।" in f_same.calls[0][1].messages[0]["content"]


async def test_off_mode_and_failed_reply_keep_the_draft(maker, session):
    await prepare(session, mode="off")
    row = await add(session)
    status, c, _ = await run(maker, row, {})
    assert status == "ignored" and "switched off" in c.error
    async with maker() as s:
        assert (await s.execute(select(PostComment.status))).scalar_one() == "ignored"



@respx.mock
async def test_attack_on_the_organisation_alerts_staff_and_is_hidden_only_when_sure(maker, session):
    await prepare(session, comment_against_action="hide", alert_webhook_url="https://hooks.example/x")
    alert = respx.post("https://hooks.example/x").mock(return_value=httpx.Response(200))
    hide = respx.post(url__regex=rf"{G}/C\d$").mock(return_value=httpx.Response(200, json={"success": True}))
    sure = await add(session, comment_id="C1", message="Quantum ekta bhondo cult, keu jaben na")
    unsure = await add(session, comment_id="C2", message="eder kaj kortobba ki asholei thik?")
    s1, c1, _ = await run(maker, sure, {"cheap": classify("against", reason="calls it a cult", confidence=0.95)})
    s2, c2, _ = await run(maker, unsure, {"cheap": classify("against", confidence=0.5)})
    assert (s1, c1.kind, c1.reason, c1.reply_text, c1.liked) == ("hidden", "against", "calls it a cult", None, False)
    assert s2 == "needs_staff" and c2.action == "hide" and "not sure" in c2.error
    assert len(hide.calls) == 1 and len(alert.calls) == 2                     # staff are told about both
    assert "calls it a cult" in json.loads(alert.calls[0].request.content)["text"]


@respx.mock
async def test_against_with_notify_leaves_it_for_staff_who_can_delete_or_hide_and_show_it_again(client, maker, session):
    await prepare(session)                                                    # comment_against_action "notify" by default
    row = await add(session, comment_id="C1", message="Eta shudhu taka kamanor business")
    status, c, _ = await run(maker, row, {"cheap": classify("against")})
    assert status == "needs_staff" and c.action == "notify" and c.reply_text is None
    staff = await login(client, maker, "agent", "agent@x.com")
    respx.post(f"{G}/C1").mock(return_value=httpx.Response(200, json={"success": True}))
    respx.delete(f"{G}/C1").mock(return_value=httpx.Response(200, json={"success": True}))
    assert (await client.post(f"/api/comments/{row}/hide", headers=staff)).json()["status"] == "hidden"
    assert (await client.post(f"/api/comments/{row}/unhide", headers=staff)).json()["status"] == "ignored"
    assert (await client.post(f"/api/comments/{row}/delete", headers=staff)).json()["status"] == "deleted"
    assert (await client.post(f"/api/comments/{row}/hide", headers=staff)).status_code == 409


async def test_check_a_comment_tool_says_what_it_is_and_why(client, maker, session):
    await prepare(session)
    staff = await login(client, maker, "agent", "agent@x.com")
    fake = FakeLLM({"cheap": classify("spam", reason="sells loans with a link", confidence=0.9)})
    import app.llm.service as svc
    old, svc.default_factory = svc.default_factory, fake
    try:
        r = (await client.post("/api/comments/check", json={"text": "Loan nite click korun bit.ly/x"}, headers=staff)).json()
    finally:
        svc.default_factory = old
    assert r == {"kind": "spam", "reason": "sells loans with a link", "confidence": 0.9, "language": "bn"}


@respx.mock
async def test_scan_reads_older_comments_moderates_them_and_only_drafts_replies(client, maker, session):
    await prepare(session, mode="auto")
    staff = await login(client, maker, "agent", "agent@x.com")
    respx.get(f"{G}/PAGE1/posts").mock(return_value=httpx.Response(200, json={"data": [{"id": "PAGE1_POST1"}]}))
    respx.get(f"{G}/PAGE1_POST1/comments").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "C1", "message": "fee koto?", "from": {"id": "U1", "name": "A"}},
        {"id": "C2", "message": "our reply", "from": {"id": "PAGE1"}}]}))
    r = (await client.post("/api/comments/scan", json={"posts": 5}, headers=staff)).json()
    assert r == {"posts": 5, "accounts": 1, "comments": 1, "new": 1, "errors": []}
    assert (await client.post("/api/comments/scan", json={"posts": 5}, headers=staff)).json()["new"] == 0
    async with maker() as s:
        row = (await s.execute(select(PostComment))).scalar_one()
    like = respx.post(f"{G}/C1/likes").mock(return_value=httpx.Response(200, json={"success": True}))
    status, c, _ = await run(maker, row.id, {"cheap": classify("question", "fee koto?")})
    assert (status, c.source, like.called) == ("draft", "scan", False) and c.reply_text
