from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import Conversation, Message, WebhookEvent
from app.services import ingest
from app.services.ingest import acknowledge_event, process_event
from app.services.reply_engine import ReplyResult
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import configure_channel, mk_msg
from tests.test_webhooks import messenger_payload, post

GRAPH = "https://graph.facebook.com/v21.0/me/messages"


@pytest.fixture(autouse=True)
async def _debounce_on(maker):
    """Debounce ships switched off (default 0): these tests are about it, so switch it on explicitly."""
    async with maker() as s:
        await set_setting(s, "debounce_seconds", 3)
        await s.commit()


@pytest.fixture
def asked(monkeypatch):
    """Stand-in for the answer pipeline: records the question each reply was built from."""
    questions: list[tuple[str, int]] = []

    async def fake_generate_reply(session, r, text, llm=None, *, knowledge_only=False, combined_turns=1, **_):
        questions.append((text, combined_turns))
        return ReplyResult(f"answer to: {text}", "llm", debug={})
    monkeypatch.setattr(ingest, "generate_reply", fake_generate_reply)
    return questions


async def add_event(maker, text, mid, ago_s=0.0, uid="PSID_D"):
    ts = datetime.now(timezone.utc) - timedelta(seconds=ago_s)
    async with maker() as s:
        ev = WebhookEvent(channel="messenger", external_id=mid, sender_id=uid,
                          payload=mk_msg(uid=uid, text=text, mid=mid, ts=ts).to_json(), received_at=ts)
        s.add(ev)
        await s.commit()
        return ev.id


async def replies(maker):
    async with maker() as s:
        return (await s.execute(select(Message.content_redacted).where(Message.role == "assistant").order_by(Message.id))).scalars().all()


async def test_a_burst_gets_one_reply_built_from_all_messages(maker, asked):
    ids = [await add_event(maker, "hi", "b1", 2.0), await add_event(maker, "course er", "b2", 1.0),
           await add_event(maker, "fee koto?", "b3", 0.0)]
    assert [await process_event(maker, i) for i in ids] == ["done", "done", "done"]
    assert asked == [("hi\ncourse er\nfee koto?", 3)]
    assert len(await replies(maker)) == 1
    async with maker() as s:  # every message is still stored
        assert len((await s.execute(select(Message).where(Message.role == "user"))).scalars().all()) == 3


async def test_messages_far_apart_get_separate_replies(maker, asked):
    await process_event(maker, await add_event(maker, "fee koto?", "f1", 15.0))   # answered before the next one came
    await process_event(maker, await add_event(maker, "kobe shuru?", "f2", 0.0))
    assert asked == [("fee koto?", 1), ("kobe shuru?", 1)]


async def test_a_long_burst_is_answered_after_the_maximum_wait(maker, asked):
    first = await add_event(maker, "fee koto?", "c1", 15.0)   # older than debounce_max_seconds (10)
    second = await add_event(maker, "ar schedule?", "c2", 0.0)
    await process_event(maker, first)                          # a newer message exists, but the guest waited enough
    await process_event(maker, second)
    assert [q for q, _ in asked] == ["fee koto?", "ar schedule?"]


async def test_debounce_off_restores_one_reply_per_message(maker, asked):
    async with maker() as s:
        await set_setting(s, "debounce_seconds", 0)
        await s.commit()
    ids = [await add_event(maker, "course er bepare", "o1", 1.0), await add_event(maker, "fee koto?", "o2", 0.0)]
    for i in ids:
        await process_event(maker, i)
    assert [q for q, _ in asked] == ["course er bepare", "fee koto?"]


async def test_handover_and_erasure_are_seen_on_every_message_of_a_burst(maker, asked):
    ids = [await add_event(maker, "agent please", "h1", 1.0), await add_event(maker, "fee koto?", "h2", 0.0)]
    for i in ids:
        await process_event(maker, i)
    async with maker() as s:
        assert (await s.execute(select(Conversation.ai_paused))).scalar_one() is True
    assert DEFAULTS["handover_ack"] in await replies(maker)  # "agent please" is two words: Bangla, the default

    ids = [await add_event(maker, "delete my data", "e1", 1.0, uid="PSID_E"),
           await add_event(maker, "fee koto?", "e2", 0.0, uid="PSID_E")]
    for i in ids:
        await process_event(maker, i)
    assert DEFAULTS["erasure_ack_en"] in await replies(maker)
    assert asked[-1] == ("fee koto?", 1)  # the deleted message never comes back as part of the question


async def test_test_chat_is_never_debounced(maker, asked):
    ids = [await add_event(maker, "course er bepare", "t1", 1.0, uid="test:dbn"),
           await add_event(maker, "fee koto?", "t2", 0.0, uid="test:dbn")]
    for i in ids:
        await process_event(maker, i)
    assert [q for q, _ in asked] == ["course er bepare", "fee koto?"]


@respx.mock
async def test_webhook_defers_the_reply_but_shows_typing_at_once(client, maker):
    async with maker() as s:
        await configure_channel(s)
    await post(client, "messenger", messenger_payload(mid="w1", sender="PSID_W"))
    q = client.app.state.queue
    assert [j[0] for j in q.jobs] == ["ack_event_job", "process_event_job"]
    assert q.options[1]["_defer_by"] == timedelta(seconds=3) and "_defer_by" not in q.options[0]
    async with maker() as s:
        ev = (await s.execute(select(WebhookEvent))).scalar_one()
        assert ev.sender_id == "PSID_W"
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={}))
    await acknowledge_event(maker, ev.id)
    actions = [c.request.content for c in route.calls]
    assert any(b"mark_seen" in a for a in actions) and any(b"typing_on" in a for a in actions)

    async with maker() as s:
        await set_setting(s, "debounce_seconds", 0)
        await s.commit()
    await post(client, "messenger", messenger_payload(mid="w2", sender="PSID_W"))
    assert q.jobs[-1][0] == "process_event_job" and "_defer_by" not in q.options[-1]
