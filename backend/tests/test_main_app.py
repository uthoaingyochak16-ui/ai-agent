"""The main application: its open programs (with registration links) come in, interested guests go out as leads."""
import json
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Lead, Profile, Program
from app.rag.programs import context_line, today_bd
from app.services import leads, main_app
from app.services import consent as consent_svc

from tests.test_admin_api2 import admin  # noqa: F401,E402 - fixture

API = "http://main-app.test"


@pytest.fixture
def connected(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "main_app_api_url", API)
    monkeypatch.setattr(s, "main_app_api_key", "qf_live_test")
    monkeypatch.setattr(s, "main_app_public_url", "https://app.example.org")


def program(pid, *, days=10, **kw):
    when = (today_bd() + timedelta(days=days)).isoformat() + "T10:00:00"
    return {"id": pid, "name": f"P{pid}", "title": f"Program {pid}", "event_date": when, "display_date": "", "place": "Dhaka",
            "fee": 500, "seats_full": False, "helpline": "", "note": "", "registration_path": f"/register?q={pid}", **kw}


@respx.mock
async def test_open_programs_come_in_with_their_links_and_closed_ones_go(session, connected):
    route = respx.get(f"{API}/api/partner/v1/programs").mock(return_value=httpx.Response(200, json={"programs": [program("a1"), program("b2")]}))
    assert (await main_app.sync_programs(session))["programs"] == 2
    assert route.calls[0].request.headers["authorization"] == "Bearer qf_live_test"
    rows = {p.external_id: p for p in (await session.execute(select(Program))).scalars()}
    assert rows["a1"].registration_url == "https://app.example.org/register?q=a1" and rows["a1"].fee == "500 টাকা"
    assert "registration link: https://app.example.org/register?q=a1" in context_line(rows["a1"], today_bd())

    route.mock(return_value=httpx.Response(200, json={"programs": [program("a1", place="Chattogram")]}))   # b2 closed
    await main_app.sync_programs(session)
    rows = {p.external_id: p for p in (await session.execute(select(Program))).scalars()}
    assert set(rows) == {"a1"} and rows["a1"].place == "Chattogram"


@respx.mock
async def test_a_failed_sync_keeps_the_programs(session, connected):
    respx.get(f"{API}/api/partner/v1/programs").mock(return_value=httpx.Response(200, json={"programs": [program("a1")]}))
    await main_app.sync_programs(session)
    respx.get(f"{API}/api/partner/v1/programs").mock(return_value=httpx.Response(503))
    assert (await main_app.sync_programs(session))["status"] == "error"
    assert (await session.execute(select(Program.external_id))).scalars().all() == ["a1"]


async def _guest(session, *, phone=None, name="Rahim"):
    p = Profile(name=name)
    session.add(p)
    await session.flush()
    if phone:
        await consent_svc.apply_field(session, p, "phone", phone, consent_checked=True)
    await session.commit()
    return p


@respx.mock
async def test_a_lead_waits_for_a_phone_number_then_reaches_the_main_app(session, connected):
    sent = respx.post(f"{API}/api/partner/v1/leads").mock(return_value=httpx.Response(201, json={"guest_id": "g1", "task_id": "t1"}))
    p = await _guest(session)
    lead = await leads.record(session, p, None, interest="Quantum Method course", note="asked about Dhaka batch")
    assert lead.status == "pending" and "phone" in lead.error and not sent.called
    await consent_svc.apply_field(session, p, "phone", "01712345678", consent_checked=True)
    await session.commit()
    assert await leads.send_pending(session) == 1
    body = json.loads(sent.calls[0].request.content)
    assert body["full_name"] == "Rahim" and body["mobile"].endswith("1712345678") and "Quantum Method course" in body["note"]
    lead = (await session.execute(select(Lead))).scalar_one()
    assert (lead.status, lead.main_app_task_id) == ("sent", "t1")
    # the same interest again soon: the same lead, not a second task in the main app
    again = await leads.record(session, p, None, interest="Quantum Method course")
    assert again.id == lead.id and sent.call_count == 1


@respx.mock
async def test_an_unreachable_main_app_keeps_the_lead_pending(session, connected):
    respx.post(f"{API}/api/partner/v1/leads").mock(side_effect=httpx.ConnectError("down"))
    p = await _guest(session, phone="01712345678")
    lead = await leads.record(session, p, None, interest="Yoga orientation")
    assert lead.status == "pending" and "unreachable" in lead.error and lead.attempts == 1


async def test_without_a_main_app_the_lead_list_still_works(session):
    p = await _guest(session, phone="01712345678")
    lead = await leads.record(session, p, None, interest="Sadakaion")
    assert lead.status == "off"


async def test_leads_api_lists_them(client, admin, session, connected):
    p = await _guest(session)
    await leads.record(session, p, None, interest="Quantum Method course")
    await session.commit()
    r = (await client.get("/api/leads", headers=admin)).json()
    assert r["items"][0]["guest"] == "Rahim" and r["items"][0]["status"] == "pending" and r["main_app"]["connected"]
    assert r["counts"]["pending"] == 1
