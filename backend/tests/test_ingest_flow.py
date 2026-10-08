import json
from datetime import datetime, timedelta, timezone

import httpx
import respx
from sqlalchemy import select

from app.models import Conversation, Message, Profile, QAPair, WebhookEvent
from app.rag.templates import normalize_question
from app.services import consent as C
from app.services.ingest import handle_message, process_event
from tests.conftest import configure_channel, mk_msg
from tests.test_webhooks import messenger_payload, post

GRAPH = "https://graph.facebook.com/v21.0/me/messages"


def sent_bodies(route):
    return [json.loads(c.request.content) for c in route.calls]


async def msgs(session, role=None):
    q = select(Message).order_by(Message.id)
    if role:
        q = q.where(Message.role == role)
    return (await session.execute(q)).scalars().all()


@respx.mock
async def test_full_flow_webhook_to_worker_to_reply(client, maker):
    async with maker() as s:
        await configure_channel(s)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "m_out_1"}))
    await post(client, "messenger", messenger_payload(mid="m_a", text="what are your hours? call 01712345678"))
    async with maker() as s:
        ev_id = (await s.execute(select(WebhookEvent.id))).scalar_one()
    # the worker runs the queued job
    assert await process_event(maker, ev_id) == "done"
    async with maker() as s:
        ms = await msgs(s)
        assert [m.role for m in ms] == ["user", "assistant"]
        assert "01712345678" not in ms[0].content_redacted and "[PHONE_1]" in ms[0].content_redacted
        assert ms[1].delivery_status == "sent" and ms[1].external_message_id == "m_out_1"
    bodies = sent_bodies(route)
    # one mark_seen + one reply
    assert any(b.get("sender_action") == "mark_seen" for b in bodies)
    reply = next(b for b in bodies if "message" in b)
    assert reply["recipient"]["id"] == "PSID1" and reply["messaging_type"] == "RESPONSE"
    assert route.calls[0].request.headers["authorization"] == "Bearer TOKEN"


@respx.mock
async def test_reprocessing_the_same_event_is_idempotent(client, maker):
    async with maker() as s:
        await configure_channel(s)
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await post(client, "messenger", messenger_payload(mid="m_b"))
    async with maker() as s:
        ev_id = (await s.execute(select(WebhookEvent.id))).scalar_one()
    await process_event(maker, ev_id)
    assert await process_event(maker, ev_id) == "skipped"
    async with maker() as s:
        assert len(await msgs(s, "user")) == 1
        # even a forced second pass over the same message id does not duplicate the user message
        await handle_message(s, mk_msg("messenger", "PSID1", "hello", "m_b"))
        assert len(await msgs(s, "user")) == 1


@respx.mock
async def test_handover_keyword_pauses_ai_and_sends_ack_only(session):
    await configure_channel(session)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await handle_message(session, mk_msg(text="I want to talk to a human agent please", mid="h1"))
    conv = (await session.execute(select(Conversation))).scalar_one()
    assert conv.ai_paused and conv.status == "handover" and conv.handover_reason.startswith("keyword:")
    replies = [b for b in sent_bodies(route) if "message" in b]
    assert len(replies) == 1 and "team member" in replies[0]["message"]["text"]
    # follow-up while paused: stored, but no AI reply and no new outbound call
    before = len(route.calls)
    await handle_message(session, mk_msg(text="hello??", mid="h2", ts=datetime.now(timezone.utc)))
    after_calls = [c for c in list(route.calls)[before:] if b"message" in c.request.content and b"sender_action" not in c.request.content]
    assert after_calls == []
    assert len(await msgs(session, "user")) == 2 and len(await msgs(session, "assistant")) == 1


@respx.mock
async def test_paused_handover_still_answers_saved_qa(session):
    await configure_channel(session)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    question = "Quantum method course fee koto?"
    session.add(QAPair(question=question, question_norm=normalize_question(question),
                       answer_template="The course fee is 11,500 BDT.", category="fees",
                       embedding=[0.0] * 1024))
    await session.commit()

    await handle_message(session, mk_msg(text="Agent", mid="hk1"))
    before = len(route.calls)
    await handle_message(session, mk_msg(text=question, mid="hk2", ts=datetime.now(timezone.utc)))

    replies = [c for c in list(route.calls)[before:] if b"message" in c.request.content and b"sender_action" not in c.request.content]
    assert len(replies) == 1
    assert "11,500 BDT" in json.loads(replies[0].request.content)["message"]["text"]
    conv = (await session.execute(select(Conversation))).scalar_one()
    assert conv.ai_paused and conv.status == "handover"


@respx.mock
async def test_out_of_window_reply_is_blocked_and_flagged(session):
    await configure_channel(session)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    old = datetime.now(timezone.utc) - timedelta(days=2)  # webhook delivered very late
    await handle_message(session, mk_msg(text="hello", mid="old1", ts=old))
    bots = await msgs(session, "assistant")
    assert len(bots) == 1 and bots[0].delivery_status == "blocked" and bots[0].flag == "out_of_window"
    assert [b for b in sent_bodies(route) if "message" in b] == []


@respx.mock
async def test_meta_window_error_is_mapped_and_recorded(session):
    await configure_channel(session)
    respx.post(GRAPH).mock(side_effect=lambda req: httpx.Response(200, json={}) if b"mark_seen" in req.content else
                           httpx.Response(400, json={"error": {"code": 10, "error_subcode": 2018278, "message": "outside window"}}))
    await handle_message(session, mk_msg(text="hello", mid="w1"))
    bot = (await msgs(session, "assistant"))[0]
    assert bot.delivery_status == "blocked" and bot.flag == "out_of_window"


@respx.mock
async def test_transient_graph_errors_are_retried_with_backoff(session, monkeypatch):
    import app.core.retry as retry
    async def no_sleep(_): return None
    monkeypatch.setattr(retry.asyncio, "sleep", no_sleep)
    await configure_channel(session)
    calls = {"n": 0}

    def handler(req):
        if b"mark_seen" in req.content or b"typing_on" in req.content:
            return httpx.Response(200, json={})
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"message": "boom"}}) if calls["n"] < 3 else httpx.Response(200, json={"message_id": "ok"})
    respx.post(GRAPH).mock(side_effect=handler)
    await handle_message(session, mk_msg(text="hello", mid="r1"))
    bot = (await msgs(session, "assistant"))[0]
    assert calls["n"] == 3 and bot.delivery_status == "sent"


@respx.mock
async def test_unconfigured_channel_marks_reply_failed_without_crashing(session):
    await handle_message(session, mk_msg(text="hello", mid="n1"))
    bot = (await msgs(session, "assistant"))[0]
    assert bot.delivery_status == "failed"


@respx.mock
async def test_consent_conversation_flow_saves_phone_after_yes(session):
    await configure_channel(session)
    respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await handle_message(session, mk_msg(text="my phone is 01712345678", mid="c1"))
    p = (await session.execute(select(Profile))).scalar_one()
    assert p.phone_enc is None
    C.mark_consent_asked(p)  # the assistant asked "may I save your details?"
    await session.commit()
    await handle_message(session, mk_msg(text="yes", mid="c2"))
    p = (await session.execute(select(Profile))).scalar_one()
    assert p.consent_given and C.get_phone(p) == "+8801712345678"


@respx.mock
async def test_delete_my_data_via_chat(session):
    await configure_channel(session)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await handle_message(session, mk_msg(text="hello", mid="d1"))
    p = (await session.execute(select(Profile))).scalar_one()
    await C.record_consent(session, p, True)
    await C.apply_field(session, p, "name", "Rahim")
    await session.commit()
    await handle_message(session, mk_msg(text="Please delete my data", mid="d2"))
    await session.refresh(p)
    assert p.name is None and p.erased_at is not None
    assert any("deleted" in (b.get("message") or {}).get("text", "") for b in sent_bodies(route))
    history = await msgs(session)
    # everything said before the erasure is wiped; only the final confirmation (sent after) remains readable
    assert all(m.content_redacted == "[deleted at user's request]" for m in history[:-1])
    assert history[-1].role == "assistant" and "deleted" in history[-1].content_redacted
