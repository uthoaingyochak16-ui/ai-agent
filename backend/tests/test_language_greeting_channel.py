from datetime import datetime, timedelta, timezone

import httpx
import respx
from sqlalchemy import select

from app.models import Message, WebhookEvent
from app.services.ingest import handle_message
from app.services.language import detect
from app.services.settings_service import DEFAULTS
from tests.conftest import mk_msg
from tests.test_admin_api2 import G, admin  # noqa: F401 - fixture


def test_one_reply_language_per_message():
    for t in ("Course fee koto?", "apnara ki niye kaj koren", "কোর্স ফি কত?", "ami jante chai", "😀"):
        assert detect(t) == "bn", t
    for t in ("What is the course fee?", "Who are you?", "How can I join the meditation course"):
        assert detect(t) == "en", t
    # canned texts are single-language now
    for k in ("no_answer_reply", "handover_ack", "consent_prompt", "erasure_ack"):
        assert not any("a" <= c.lower() <= "z" for c in DEFAULTS[k].replace("agent", "")), k
        assert not any("ঀ" <= c <= "৿" for c in DEFAULTS[f"{k}_en"]), k


async def bot(s):
    return [m.content_redacted for m in (await s.execute(select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def test_second_hi_gets_a_short_reply_and_unknowns_answer_in_the_guests_language(maker):
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    async with maker() as s:
        await handle_message(s, mk_msg(uid="test:l", text="Hello", mid="l1", ts=t0))
        await handle_message(s, mk_msg(uid="test:l", text="Hi", mid="l2", ts=t0 + timedelta(minutes=20)))
        await handle_message(s, mk_msg(uid="test:l", text="Course fee koto?", mid="l3", ts=t0 + timedelta(minutes=21)))
        await handle_message(s, mk_msg(uid="test:l", text="What is the course fee?", mid="l4", ts=t0 + timedelta(minutes=22)))
        await handle_message(s, mk_msg(uid="test:l", text="Who are you?", mid="l5", ts=t0 + timedelta(minutes=23)))
        out = await bot(s)
    assert out[0] == DEFAULTS["greeting_first_reply"]
    assert out[1] == DEFAULTS["greeting_repeat_reply"]            # never "I don't know" for a greeting
    assert out[2] == DEFAULTS["no_answer_reply"]                  # Banglish -> Bangla only
    assert out[3] == DEFAULTS["no_answer_reply_en"]               # English -> English only
    assert out[4].startswith("I'm the Quantum Method AI Assistant")


@respx.mock
async def test_channel_ok_when_messages_arrive_even_without_pages_manage_metadata(client, admin, maker):  # noqa: F811
    await client.put("/api/channels/messenger", headers=admin, json={
        "account_id": "PAGE_A", "access_token": "TOK", "app_secret": "SEC", "enabled": True})
    async with maker() as s:
        s.add(WebhookEvent(channel="messenger", external_id="m1", payload={}, status="done"))
        await s.commit()

    def me(req):
        import hashlib
        import hmac
        proof = req.url.params.get("appsecret_proof")
        if proof and proof != hmac.new(b"SEC", b"TOK", hashlib.sha256).hexdigest():
            return httpx.Response(400, json={"error": {"code": 100, "message": "Invalid appsecret_proof"}})
        return httpx.Response(200, json={"id": "PAGE_A", "name": "Quantum Method Course"})
    respx.get(f"{G}/me").mock(side_effect=me)
    respx.get(f"{G}/me/subscribed_apps").mock(return_value=httpx.Response(403, json={"error": {
        "code": 200, "message": "(#200) Requires pages_manage_metadata permission to manage the object"}}))
    r = (await client.post("/api/channels/messenger/test", headers=admin)).json()
    assert r["status"] == "ok" and "Receiving messages" in r["status_detail"]
