import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import respx
from sqlalchemy import func, select

from app.models import ChannelIdentity, Conversation, Message, Profile, ProfileFact, WebhookEvent
from app.services.greeting import is_greeting
from app.services.ingest import handle_message
from app.services.retention import purge_old_conversations
from app.services.settings_service import DEFAULTS
from tests.conftest import configure_channel, mk_msg

GRAPH = "https://graph.facebook.com/v21.0/me/messages"
FIRST, RETURN = DEFAULTS["greeting_first_reply"], DEFAULTS["greeting_return_reply"]
WORDS = DEFAULTS["greeting_words"]


def test_is_greeting():
    for t in ("hi", "Hi!", "hiii", "Hellooo 👋", "hye", "hey there", "hi bhai", "Assalamu Alaikum", "আসসালামু আলাইকুম",
              "salam", "হাই", "hello hi", "Good morning"):
        assert is_greeting(t, WORDS), t
    for t in ("hi, what is the course fee?", "hello I want to join", "price?", "thanks", "", "help"):
        assert not is_greeting(t, WORDS), t


async def assistant_texts(session):
    return [m.content_redacted for m in (await session.execute(
        select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def test_first_greeting_introduces_the_assistant_then_return_greeting_after_5h(maker):
    t0 = datetime.now(timezone.utc) - timedelta(hours=12)
    async with maker() as s:
        await handle_message(s, mk_msg(channel="messenger", uid="test:g1", text="hi", mid="g1", ts=t0))
        assert await assistant_texts(s) == [FIRST]
    async with maker() as s:  # within 5 h: not a canned greeting again (normal pipeline answers)
        await handle_message(s, mk_msg(channel="messenger", uid="test:g1", text="hello", mid="g2", ts=t0 + timedelta(hours=1)))
        texts = await assistant_texts(s)
        assert len(texts) == 2 and texts[1] not in (FIRST, RETURN)
    async with maker() as s:  # after a 5 h break: short greeting, no "I'm an AI assistant"
        await handle_message(s, mk_msg(channel="messenger", uid="test:g1", text="Assalamu alaikum", mid="g3", ts=t0 + timedelta(hours=7)))
        assert (await assistant_texts(s))[-1] == RETURN


async def test_question_is_not_answered_with_a_greeting(maker):
    async with maker() as s:
        await handle_message(s, mk_msg(uid="test:g2", text="hi, what is the course fee?", mid="q1"))
        assert FIRST not in await assistant_texts(s)


async def test_payment_question_is_handed_to_a_human_agent(maker):
    async with maker() as s:
        r = await handle_message(s, mk_msg(uid="test:payment", text="Payment method ki?", mid="payment-1"))
        assert r.conversation.ai_paused and r.conversation.status == "handover"
        assert r.conversation.handover_reason == "payment_request"
        assert (await assistant_texts(s))[-1] == DEFAULTS["handover_ack"]


@respx.mock
async def test_typing_bubble_and_messenger_profile_photo(maker):
    async with maker() as s:
        await configure_channel(s)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "out"}))
    respx.get("https://graph.facebook.com/v21.0/PSID9").mock(return_value=httpx.Response(
        200, json={"first_name": "Rahim", "last_name": "Uddin", "profile_pic": "https://cdn.example/p.jpg", "id": "PSID9"}))
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PSID9", text="hi", mid="t1"))
        ident = (await s.execute(select(ChannelIdentity))).scalar_one()
        assert ident.display_name == "Rahim Uddin" and ident.avatar_url == "https://cdn.example/p.jpg"
    actions = [json.loads(c.request.content).get("sender_action") for c in route.calls]
    assert "mark_seen" in actions and "typing_on" in actions
    # typing is shown before the reply is sent
    assert actions.index("typing_on") < next(i for i, c in enumerate(route.calls) if "message" in json.loads(c.request.content))


@respx.mock
async def test_whatsapp_typing_indicator_uses_the_customer_message(maker):
    async with maker() as s:
        await configure_channel(s, channel="whatsapp", account_id="PN1")
    route = respx.post("https://graph.facebook.com/v21.0/PN1/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out"}]}))
    async with maker() as s:
        await handle_message(s, mk_msg(channel="whatsapp", uid="8801712345678", text="hello", mid="wamid.in", name="Karim"))
        assert (await s.execute(select(ChannelIdentity.display_name))).scalar_one() == "Karim"
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert any(b.get("typing_indicator") == {"type": "text"} and b.get("message_id") == "wamid.in" for b in bodies)


async def test_no_typing_when_a_person_has_taken_over(maker):
    async with maker() as s:
        await configure_channel(s)
    with respx.mock(assert_all_called=False) as m:
        route = m.post(GRAPH).mock(return_value=httpx.Response(200, json={}))
        m.get(url__regex=r".*/PSID7.*").mock(return_value=httpx.Response(200, json={}))
        async with maker() as s:
            r = await handle_message(s, mk_msg(uid="PSID7", text="hi", mid="p1"))
            r.conversation.ai_paused = True
            await s.commit()
        before = len(route.calls)
        async with maker() as s:
            await handle_message(s, mk_msg(uid="PSID7", text="anyone?", mid="p2"))
        await asyncio.sleep(0)
        later = [json.loads(c.request.content) for c in list(route.calls)[before:]]
        assert [b.get("sender_action") for b in later] == ["mark_seen"]  # read receipt only: no bubble, no reply


async def test_retention_deletes_old_chats_but_keeps_the_profile(maker):
    old = datetime.now(timezone.utc) - timedelta(days=31)
    async with maker() as s:
        r = await handle_message(s, mk_msg(uid="test:r1", text="hi", mid="r1"))
        s.add(ProfileFact(profile_id=r.profile.id, key="location", value="Dhaka"))
        stale = Conversation(channel_identity_id=r.identity.id, profile_id=r.profile.id, last_message_at=old)
        s.add(stale)
        await s.flush()
        s.add(Message(conversation_id=stale.id, role="user", content_redacted="old", created_at=old))
        s.add(Message(conversation_id=r.conversation.id, role="user", content_redacted="old in live chat", created_at=old))
        s.add(WebhookEvent(channel="messenger", external_id="old-ev", payload={}, status="done", received_at=old))
        await s.commit()
        profile_id, live_id = r.profile.id, r.conversation.id

    async with maker() as s:
        out = await purge_old_conversations(s)
        assert out["messages"] == 2 and out["conversations"] == 1 and out["webhook_events"] == 1
        assert [c.id for c in (await s.execute(select(Conversation))).scalars()] == [live_id]
        assert "old" not in [m.content_redacted for m in (await s.execute(select(Message))).scalars()]
        assert (await s.execute(select(func.count()).select_from(Message))).scalar_one() == 2  # today's hi + reply
        assert await s.get(Profile, profile_id) is not None
        assert (await s.execute(select(ProfileFact.value))).scalar_one() == "Dhaka"
        assert (await s.execute(select(func.count()).select_from(ChannelIdentity))).scalar_one() == 1


@respx.mock
async def test_replies_off_still_reads_the_guest_but_sends_nothing(maker):
    from app.models import ChannelConfig
    async with maker() as s:
        await configure_channel(s)
        (await s.execute(select(ChannelConfig))).scalar_one().enabled = False   # "Replies enabled" switched off
        await s.commit()
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "out"}))
    respx.get("https://graph.facebook.com/v21.0/PSIDOFF").mock(return_value=httpx.Response(
        200, json={"first_name": "Rahim", "last_name": "Uddin", "profile_pic": "https://cdn.example/r.jpg"}))
    async with maker() as s:
        await handle_message(s, mk_msg(uid="PSIDOFF", text="hi", mid="off1"))
        ident = (await s.execute(select(ChannelIdentity))).scalar_one()
        assert ident.display_name == "Rahim Uddin" and ident.avatar_url == "https://cdn.example/r.jpg"
        bot = (await s.execute(select(Message).where(Message.role == "assistant"))).scalar_one()
        assert bot.delivery_status == "failed" and bot.flag == "replies_off"
    actions = [json.loads(c.request.content).get("sender_action") for c in route.calls]
    assert "typing_on" not in actions and not any("message" in json.loads(c.request.content) for c in route.calls)
