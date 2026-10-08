import json

import httpx
import respx
from sqlalchemy import select

from app.core.security import hash_password
from app.models import AdminUser, Conversation, Message
from app.services.ingest import handle_message
from tests.conftest import configure_channel, mk_msg

GRAPH = "https://graph.facebook.com/v21.0"


async def login(client, maker, role="admin", email="a@x.com"):
    async with maker() as s:
        s.add(AdminUser(email=email, name="A", role=role, password_hash=hash_password("correct-horse-battery")))
        await s.commit()
    r = await client.post("/api/auth/login", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 200
    return {"authorization": f"Bearer {r.json()['access_token']}"}


async def test_auth_required_and_wrong_password(client, maker):
    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get("/api/conversations", headers={"authorization": "Bearer junk"})).status_code == 401
    async with maker() as s:
        s.add(AdminUser(email="a@x.com", role="admin", password_hash=hash_password("correct-horse-battery")))
        await s.commit()
    assert (await client.post("/api/auth/login", json={"email": "a@x.com", "password": "bad"})).status_code == 401


async def test_agent_cannot_use_admin_endpoints(client, maker):
    h = await login(client, maker, role="agent", email="ag@x.com")
    assert (await client.get("/api/conversations", headers=h)).status_code == 200
    assert (await client.get("/api/channels", headers=h)).status_code == 403
    assert (await client.post("/api/profiles/merge", json={"keep_id": 1, "merge_id": 2}, headers=h)).status_code == 403


@respx.mock
async def test_agent_takeover_reply_pauses_ai_and_sends(client, maker):
    async with maker() as s:
        await configure_channel(s)
        respx.post(f"{GRAPH}/me/messages").mock(return_value=httpx.Response(200, json={"message_id": "mid_h"}))
        await handle_message(s, mk_msg(text="hello", mid="a1"))
        conv_id = (await s.execute(select(Conversation.id))).scalar_one()
    h = await login(client, maker, role="agent", email="ag@x.com")
    r = await client.post(f"/api/conversations/{conv_id}/reply", json={"text": "Hi, this is Sara from the team"}, headers=h)
    assert r.status_code == 200 and r.json()["role"] == "agent" and r.json()["delivery_status"] == "sent"
    d = (await client.get(f"/api/conversations/{conv_id}", headers=h)).json()
    assert d["ai_paused"] is True
    r2 = await client.post(f"/api/conversations/{conv_id}/resume", headers=h)
    assert r2.json()["ai_paused"] is False


@respx.mock
async def test_manual_reply_outside_window_is_409_and_whatsapp_template_allowed(client, maker):
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=3)
    async with maker() as s:
        await configure_channel(s, "whatsapp", "PN1")
        route = respx.post(f"{GRAPH}/PN1/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out"}]}))
        await handle_message(s, mk_msg("whatsapp", "8801712345678", "hello", "w1", ts=old))
        conv_id = (await s.execute(select(Conversation.id))).scalar_one()
    h = await login(client, maker)
    r = await client.post(f"/api/conversations/{conv_id}/reply", json={"text": "free text"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"] == {"error": "out_of_window", "needs_template": True}
    r = await client.post(f"/api/conversations/{conv_id}/reply", headers=h, json={
        "text": "", "template": {"name": "followup", "language": "en", "body_params": ["Karim"]}})
    assert r.status_code == 200
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "template" and body["template"]["name"] == "followup"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "Karim"


async def test_channel_config_never_returns_secrets_and_encrypts_token(client, maker):
    h = await login(client, maker)
    r = await client.put("/api/channels/messenger", headers=h, json={
        "enabled": True, "account_id": "PAGE1", "access_token": "EAAB-super-secret", "app_secret": "appsecret"})
    assert r.status_code == 200 and r.json()["has_token"] and "super-secret" not in r.text
    from app.models import ChannelConfig
    async with maker() as s:
        cfg = (await s.execute(select(ChannelConfig))).scalar_one()
        assert "super-secret" not in cfg.access_token_enc and "appsecret" not in cfg.app_secret_enc
    lst = (await client.get("/api/channels", headers=h)).json()
    assert {c["channel"] for c in lst} == {"messenger", "instagram", "whatsapp"}
    assert lst[0]["webhook_url"].endswith("/webhooks/messenger")


@respx.mock
async def test_per_channel_app_secret_overrides_default(client, maker):
    from app.channels.signature import compute_signature
    from tests.test_webhooks import messenger_payload
    h = await login(client, maker)
    await client.put("/api/channels/messenger", headers=h, json={"app_secret": "page-specific"})
    body = json.dumps(messenger_payload()).encode()
    bad = await client.post("/webhooks/messenger", content=body, headers={"x-hub-signature-256": compute_signature(body, "testsecret")})
    good = await client.post("/webhooks/messenger", content=body, headers={"x-hub-signature-256": compute_signature(body, "page-specific")})
    assert bad.status_code == 403 and good.status_code == 200


async def test_merge_endpoint_and_unmerge(client, maker):
    from app.services.identity import resolve
    async with maker() as s:
        a = await resolve(s, mk_msg("messenger", "A", "x", "1"))
        b = await resolve(s, mk_msg("instagram", "B", "x", "2"))
        await s.commit()
        ka, kb = a.profile.id, b.profile.id
    h = await login(client, maker)
    r = await client.post("/api/profiles/merge", json={"keep_id": ka, "merge_id": kb}, headers=h)
    assert r.status_code == 200
    again = await client.post("/api/profiles/merge", json={"keep_id": ka, "merge_id": kb}, headers=h)
    assert again.status_code == 409
    u = await client.post(f"/api/profiles/merge-log/{r.json()['merge_log_id']}/unmerge", headers=h)
    assert u.status_code == 200 and u.json()["warnings"] == []
    log = (await client.get("/api/profiles/merge-log", headers=h)).json()
    assert log[0]["reverted_at"] is not None
