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

from tests.test_admin_api2 import admin, agent  # noqa: F401,E402 - fixtures

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
async def test_the_ai_only_adds_to_the_list_and_staff_send_it(session, connected):
    sent = respx.post(f"{API}/api/partner/v1/leads").mock(return_value=httpx.Response(201, json={"guest_id": "g1", "task_id": "t1"}))
    p = await _guest(session, phone="01712345678")
    lead = await leads.record(session, p, None, interest="Quantum Method course", note="asked about Dhaka batch")
    assert lead.status == "new" and not sent.called                         # nothing leaves on its own
    await leads.send(session, lead)
    body = json.loads(sent.calls[0].request.content)
    assert body["full_name"] == "Rahim" and body["mobile"].endswith("1712345678") and "Dhaka batch" in body["note"]
    assert (lead.status, lead.main_app_task_id) == ("sent", "t1")
    again = await leads.record(session, p, None, interest="Quantum Method course")   # same interest: same lead
    assert again.id == lead.id and again.status == "sent"
    with pytest.raises(leads.CannotSend):
        await leads.send(session, again)                                     # never a second task


async def test_a_lead_without_a_phone_cannot_be_sent(session, connected):
    p = await _guest(session)
    lead = await leads.record(session, p, None, interest="Yoga orientation")
    with pytest.raises(leads.CannotSend, match="phone"):
        await leads.send(session, lead)
    assert lead.status == "new"


@respx.mock
async def test_an_unreachable_main_app_marks_it_failed_and_it_can_go_again(session, connected):
    route = respx.post(f"{API}/api/partner/v1/leads").mock(side_effect=httpx.ConnectError("down"))
    p = await _guest(session, phone="01712345678")
    lead = await leads.record(session, p, None, interest="Yoga orientation")
    await leads.send(session, lead)
    assert lead.status == "failed" and "unreachable" in lead.error
    route.mock(return_value=httpx.Response(201, json={"guest_id": "g2", "task_id": "t2"}))
    await leads.send(session, lead)
    assert lead.status == "sent"


async def test_without_a_main_app_leads_stay_on_the_list(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "main_app_api_url", "")   # whatever the machine's .env says
    p = await _guest(session, phone="01712345678")
    lead = await leads.record(session, p, None, interest="Sadakaion")
    with pytest.raises(leads.CannotSend, match="not connected"):
        await leads.send(session, lead)


@respx.mock
async def test_leads_api_list_send_dismiss_restore(client, admin, agent, session, connected):
    respx.post(f"{API}/api/partner/v1/leads").mock(return_value=httpx.Response(201, json={"guest_id": "g1", "task_id": "t1"}))
    a = await leads.record(session, await _guest(session, phone="01712345678"), None, interest="Quantum Method course")
    b = await leads.record(session, await _guest(session, name="Karim"), None, interest="Yoga orientation")
    await session.commit()
    r = (await client.get("/api/leads", headers=admin)).json()
    assert {i["guest"] for i in r["items"]} == {"Rahim", "Karim"} and r["counts"]["new"] == 2
    assert (await client.get("/api/leads", headers=agent)).json()["items"][-1]["phone"] != r["items"][-1]["phone"]  # masked
    res = (await client.post("/api/leads/send", headers=agent, json={"ids": [a.id, b.id]})).json()
    assert res["sent"] == 1 and "phone" in [x for x in res["results"] if x["id"] == b.id][0]["error"]
    assert (await client.patch(f"/api/leads/{b.id}", headers=agent, json={"status": "dismissed"})).json()["status"] == "dismissed"
    counts = (await client.get("/api/leads", headers=admin)).json()["counts"]
    assert (counts["sent"], counts["dismissed"], counts["new"]) == (1, 1, 0)
    assert (await client.patch(f"/api/leads/{a.id}", headers=agent, json={"status": "dismissed"})).status_code == 409
    assert (await client.patch(f"/api/leads/{b.id}", headers=agent, json={"status": "new"})).json()["status"] == "new"
