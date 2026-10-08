import json
import time

from sqlalchemy import func, select

from app.channels.signature import compute_signature
from app.models import WebhookEvent


def messenger_payload(mid="m_1", text="hi", sender="PSID1", echo=False):
    msg = {"mid": mid, "text": text}
    if echo:
        msg["is_echo"] = True
    return {"object": "page", "entry": [{"id": "PAGE1", "time": 1, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": "PAGE1"}, "timestamp": int(time.time() * 1000), "message": msg}]}]}


def whatsapp_payload(mid="wamid.1", text="hello", wa="8801712345678", statuses_only=False):
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": "PN1", "display_phone_number": "x"}}
    if statuses_only:
        value["statuses"] = [{"id": "wamid.9", "status": "delivered"}]
    else:
        value["contacts"] = [{"profile": {"name": "Karim"}, "wa_id": wa}]
        value["messages"] = [{"from": wa, "id": mid, "timestamp": str(int(time.time())), "type": "text", "text": {"body": text}}]
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA1", "changes": [{"field": "messages", "value": value}]}]}


def instagram_payload(mid="ig_1", text="hey"):
    return {"object": "instagram", "entry": [{"id": "IG1", "time": 1, "messaging": [
        {"sender": {"id": "IGSID1"}, "recipient": {"id": "IG1"}, "timestamp": int(time.time() * 1000),
         "message": {"mid": mid, "text": text}}]}]}


async def post(client, channel, payload, secret="testsecret", sign=True):
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    if sign:
        headers["x-hub-signature-256"] = compute_signature(body, secret)
    return await client.post(f"/webhooks/{channel}", content=body, headers=headers)


async def count_events(client_session_maker):
    async with client_session_maker() as s:
        return (await s.execute(select(func.count()).select_from(WebhookEvent))).scalar_one()


async def test_get_verification(client):
    ok = await client.get("/webhooks/messenger", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1234"})
    assert ok.status_code == 200 and ok.text == "1234"
    bad = await client.get("/webhooks/messenger", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403
    wrong_mode = await client.get("/webhooks/messenger", params={"hub.mode": "x", "hub.verify_token": "verify-me", "hub.challenge": "1"})
    assert wrong_mode.status_code == 403


async def test_unknown_channel_404(client):
    assert (await client.get("/webhooks/telegram")).status_code == 404


async def test_missing_or_bad_signature_rejected_and_nothing_stored(client, maker):
    assert (await post(client, "messenger", messenger_payload(), sign=False)).status_code == 403
    assert (await post(client, "messenger", messenger_payload(), secret="wrong")).status_code == 403
    assert await count_events(maker) == 0
    assert client.app.state.queue.jobs == []


async def test_valid_post_is_stored_and_enqueued(client, maker):
    r = await post(client, "messenger", messenger_payload())
    assert r.status_code == 200 and r.json()["accepted"] == 1
    assert await count_events(maker) == 1
    assert len(client.app.state.queue.jobs) == 1


async def test_duplicate_delivery_is_deduplicated(client, maker):
    await post(client, "messenger", messenger_payload(mid="m_dup"))
    r2 = await post(client, "messenger", messenger_payload(mid="m_dup"))
    assert r2.status_code == 200 and r2.json()["accepted"] == 0
    assert await count_events(maker) == 1
    assert len(client.app.state.queue.jobs) == 1


async def test_same_mid_on_different_channels_is_not_a_duplicate(client, maker):
    await post(client, "messenger", messenger_payload(mid="same"))
    await post(client, "instagram", instagram_payload(mid="same"))
    assert await count_events(maker) == 2


async def test_echoes_and_status_only_payloads_are_ignored(client, maker):
    assert (await post(client, "messenger", messenger_payload(echo=True))).json()["accepted"] == 0
    assert (await post(client, "whatsapp", whatsapp_payload(statuses_only=True))).json()["accepted"] == 0
    assert await count_events(maker) == 0


async def test_normalized_format(client, maker):
    await post(client, "whatsapp", whatsapp_payload(text="Salam"))
    async with maker() as s:
        ev = (await s.execute(select(WebhookEvent))).scalar_one()
    p = ev.payload
    assert p["channel"] == "whatsapp" and p["external_user_id"] == "8801712345678"
    assert p["text"] == "Salam" and p["message_id"] == "wamid.1" and p["attachments"] == []
    from datetime import datetime, timezone
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(p["timestamp"])).total_seconds()) < 60


async def test_db_failure_free_ack_when_queue_down(client, maker):
    class Broken:
        async def enqueue_job(self, *a, **k):
            raise ConnectionError("redis down")
    client.app.state.queue = Broken()
    r = await post(client, "messenger", messenger_payload(mid="m_q"))
    assert r.status_code == 200          # still acknowledged to Meta
    assert await count_events(maker) == 1  # durable: the requeue cron will pick it up


async def test_failed_meta_verification_is_explained_on_the_channel(client, maker):
    from app.models import ChannelConfig
    async with maker() as s:
        s.add(ChannelConfig(channel="messenger", verify_token="abcd-saved-token"))
        await s.commit()
    bad = await client.get("/webhooks/messenger", params={"hub.mode": "subscribe", "hub.verify_token": "f1d5-meta", "hub.challenge": "1"})
    assert bad.status_code == 403
    async with maker() as s:
        cfg = (await s.execute(select(ChannelConfig))).scalar_one()
        assert cfg.status == "error" and "'f1d5'" in cfg.status_detail and "'abcd'" in cfg.status_detail
    # a stray space from copy-paste still verifies, and clears the note
    ok = await client.get("/webhooks/messenger", params={"hub.mode": "subscribe", "hub.verify_token": " abcd-saved-token ", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    async with maker() as s:
        assert "verified" in (await s.execute(select(ChannelConfig))).scalar_one().status_detail
