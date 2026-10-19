import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import respx

from app.core.logging import JsonFormatter, conversation_id_var, event_id_var
from app.models import ChannelIdentity, Conversation, LLMCall, Message, WebhookEvent
from app.services.monitoring import ai_metrics, check_alerts, send_alerts, system_health
from app.services.settings_service import set_setting
from tests.test_admin_api2 import admin, agent  # noqa: F401 - fixtures

HOOK = "https://hooks.example.com/alert"


async def conv(session, ext, messages):
    ident = ChannelIdentity(channel="messenger", external_id=ext)
    session.add(ident)
    await session.flush()
    c = Conversation(channel_identity_id=ident.id)
    session.add(c)
    await session.flush()
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    for i, (role, path, delay_s) in enumerate(messages):
        session.add(Message(conversation_id=c.id, role=role, content_redacted=f"{role} {i}",
                            sources_json={"path": path} if path else None, created_at=t0 + timedelta(seconds=delay_s)))
    await session.flush()
    return c


async def test_system_health_numbers_and_states(session):
    now = datetime.now(timezone.utc)
    session.add_all([
        WebhookEvent(channel="messenger", external_id="q1", payload={}, status="queued", received_at=now - timedelta(minutes=10)),
        WebhookEvent(channel="messenger", external_id="f1", payload={}, status="failed"),
        WebhookEvent(channel="messenger", external_id="d1", payload={}, status="done"),
    ])
    c = await conv(session, "PSID_H", [("user", None, 0)])
    session.add_all([Message(conversation_id=c.id, role="assistant", content_redacted="x", delivery_status="failed"),
                     Message(conversation_id=c.id, role="assistant", content_redacted="y", delivery_status="blocked")])
    for i, (status, ms) in enumerate([("ok", 1000), ("ok", 2000), ("ok", 3000), ("error", 0), ("ok", 20000)]):
        session.add(LLMCall(task="answer", provider="anthropic", model="m", status=status, latency_ms=ms, is_test=False))
    session.add(LLMCall(task="answer", provider="anthropic", model="m", status="error", latency_ms=0, is_test=True))  # ignored
    await session.commit()
    h = await system_health(session)
    assert h["events"]["queued"] == 1 and h["events"]["failed"] == 1 and h["events"]["oldest_pending_s"] >= 600
    assert h["events"]["state"] == "red"                       # waiting longer than alert_stuck_minutes
    assert h["delivery_24h"] == {"failed": 1, "blocked": 1, "state": "amber"}
    assert h["llm_24h"]["calls"] == 5 and h["llm_24h"]["errors"] == 1 and h["llm_24h"]["error_rate"] == 0.2
    assert h["llm_24h"]["p95_latency_ms"] >= 15000 and h["llm_24h"]["state"] == "red"


async def test_ai_metrics(session):
    await conv(session, "PSID_A", [("user", None, 0), ("assistant", "llm", 4)])                      # resolved
    await conv(session, "PSID_B", [("user", None, 0), ("assistant", "no_context", 2), ("agent", None, 60)])  # handed over
    await conv(session, "PSID_C", [("user", None, 0), ("assistant", "qa_shortcut", 6)])              # resolved
    await conv(session, "test:x", [("user", None, 0), ("assistant", "no_context", 1)])                # Test chat: ignored
    session.add(LLMCall(task="answer", provider="anthropic", model="m", cost_usd=0.02, conversation_id=1, is_test=False))
    await session.commit()
    m = await ai_metrics(session, 14)
    assert m["conversations"] == 3
    assert m["resolution_rate"] == round(2 / 3, 4) and m["handover_rate"] == round(1 / 3, 4)
    assert m["no_context_rate"] == round(1 / 3, 4)
    assert m["avg_cost_per_conversation"] == 0.02 and m["median_reply_latency_s"] == 4.0


@respx.mock
async def test_alerts_fire_once_per_issue_per_hour(session):
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, json={"ok": True}))
    session.add(WebhookEvent(channel="messenger", external_id="s1", payload={}, status="queued",
                             received_at=datetime.now(timezone.utc) - timedelta(minutes=20)))
    await set_setting(session, "alert_webhook_url", HOOK)
    await session.commit()
    alerts = await check_alerts(session)
    assert [a.key for a in alerts] == ["events_stuck"]
    assert await send_alerts(session, alerts) == ["events_stuck"]
    assert await send_alerts(session, await check_alerts(session)) == []          # same issue within the hour: quiet
    assert route.call_count == 1 and "waiting more than" in json.loads(route.calls[0].request.content)["text"]

    await set_setting(session, "alert_last_sent", {"events_stuck": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()})
    await session.commit()
    assert await send_alerts(session, await check_alerts(session)) == ["events_stuck"]  # still broken an hour later: again


@respx.mock
async def test_unreachable_webhook_is_retried_next_time(session):
    respx.post(HOOK).mock(return_value=httpx.Response(500))
    await set_setting(session, "alert_webhook_url", HOOK)
    session.add(WebhookEvent(channel="messenger", external_id="s2", payload={}, status="processing",
                             received_at=datetime.now(timezone.utc) - timedelta(minutes=20)))
    await session.commit()
    assert await send_alerts(session, await check_alerts(session)) == []
    respx.post(HOOK).mock(return_value=httpx.Response(200))
    assert await send_alerts(session, await check_alerts(session)) == ["events_stuck"]  # not marked as sent before


async def test_failed_sends_alert_threshold(session):
    c = await conv(session, "PSID_F", [("user", None, 0)])
    await set_setting(session, "alert_failed_sends_per_hour", 2)
    for _ in range(3):
        session.add(Message(conversation_id=c.id, role="assistant", content_redacted="z", delivery_status="failed"))
    await session.commit()
    assert "failed_sends" in [a.key for a in await check_alerts(session)]


async def test_deep_health_is_admin_only(client, admin, agent):  # noqa: F811
    assert (await client.get("/api/health/deep", headers=agent)).status_code == 403
    r = await client.get("/api/health/deep", headers=admin)
    assert r.status_code == 200 and {"system", "ai"} <= set(r.json())


def test_log_lines_carry_event_and_conversation_ids():
    rec = logging.LogRecord("app.services.ingest", logging.INFO, __file__, 1, "replying", None, None)
    assert "event_id" not in json.loads(JsonFormatter().format(rec))
    e, c = event_id_var.set(42), conversation_id_var.set(7)
    try:
        data = json.loads(JsonFormatter().format(rec))
    finally:
        conversation_id_var.reset(c)
        event_id_var.reset(e)
    assert data["event_id"] == 42 and data["conversation_id"] == 7
