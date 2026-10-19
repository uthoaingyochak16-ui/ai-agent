"""Several Facebook Pages / Instagram accounts / WhatsApp numbers per channel: each is managed on its own, webhooks
signed by any of them are accepted, and replies go back through the account the guest wrote to."""
import json
from dataclasses import replace

import httpx
import respx
from sqlalchemy import select

from app.channels.signature import compute_signature
from app.core import crypto
from app.models import ChannelConfig, ChannelIdentity, Conversation
from app.services.ingest import handle_message
from tests.conftest import mk_msg
from tests.test_admin_api import GRAPH, login
from tests.test_webhooks import messenger_payload


async def test_accounts_can_be_added_listed_and_removed(client, maker):
    h = await login(client, maker)
    a = (await client.post("/api/channels/messenger/accounts", headers=h, json={"label": "Quantum Method", "account_id": "PAGE1",
                                                                                "access_token": "EAAB-secret-1"})).json()
    b = (await client.post("/api/channels/messenger/accounts", headers=h, json={"label": "Quantum Foundation", "account_id": "PAGE2"})).json()
    assert a["id"] != b["id"] and a["has_token"] and "secret-1" not in json.dumps(a)
    dup = await client.post("/api/channels/messenger/accounts", headers=h, json={"account_id": "PAGE2"})
    assert dup.status_code == 409                                                    # one Page is connected once
    lst = {c["channel"]: c for c in (await client.get("/api/channels", headers=h)).json()}
    assert [x["label"] for x in lst["messenger"]["accounts"]] == ["Quantum Method", "Quantum Foundation"]
    assert lst["messenger"]["account_id"] == "PAGE1" and lst["whatsapp"]["accounts"] == []   # old fields: first account
    assert (await client.put(f"/api/channels/accounts/{b['id']}", headers=h, json={"label": "QF"})).json()["label"] == "QF"
    assert (await client.delete(f"/api/channels/accounts/{b['id']}", headers=h)).status_code == 200
    lst = {c["channel"]: c for c in (await client.get("/api/channels", headers=h)).json()}
    assert [x["account_id"] for x in lst["messenger"]["accounts"]] == ["PAGE1"]


async def test_a_webhook_signed_by_any_connected_account_is_accepted(client, maker):
    async with maker() as s:
        for page, secret in (("PAGE1", "secret-one"), ("PAGE2", "secret-two")):
            s.add(ChannelConfig(channel="messenger", account_id=page, enabled=True, status="ok",
                                app_secret_enc=crypto.encrypt(secret, aad="secret:messenger")))
        await s.commit()
    body = json.dumps(messenger_payload()).encode()
    for secret, code in (("secret-one", 200), ("secret-two", 200), ("testsecret", 403)):   # the server default is overridden
        r = await client.post("/webhooks/messenger", content=body, headers={"x-hub-signature-256": compute_signature(body, secret)})
        assert r.status_code == code, secret


@respx.mock
async def test_the_reply_goes_out_through_the_number_the_guest_wrote_to(client, maker):
    async with maker() as s:
        for pn, token in (("PN1", "TOKEN-ONE"), ("PN2", "TOKEN-TWO")):
            s.add(ChannelConfig(channel="whatsapp", account_id=pn, enabled=True, status="ok",
                                access_token_enc=crypto.encrypt(token, aad="token:whatsapp")))
        await s.commit()
        one = respx.post(f"{GRAPH}/PN1/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "w.1"}]}))
        two = respx.post(f"{GRAPH}/PN2/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "w.2"}]}))
        respx.route().mock(return_value=httpx.Response(200, json={}))   # read receipts, typing
        await handle_message(s, replace(mk_msg("whatsapp", "8801712345678", "hello", "w1"), recipient_id="PN2"))
        ident = (await s.execute(select(ChannelIdentity))).scalar_one()
        assert ident.account_id == "PN2"
        conv_id = (await s.execute(select(Conversation.id))).scalar_one()
    h = await login(client, maker)
    r = await client.post(f"/api/conversations/{conv_id}/reply", json={"text": "Hi from the team"}, headers=h)
    assert r.status_code == 200 and r.json()["delivery_status"] == "sent"
    assert two.called and not one.called
    assert two.calls.last.request.headers["authorization"] == "Bearer TOKEN-TWO"
