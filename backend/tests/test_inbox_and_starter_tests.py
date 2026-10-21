"""Unified inbox (every platform, every connected Page / account) and the ready-made test set."""
from app.models import ChannelConfig, ChannelIdentity, Conversation, Message, Profile
from tests.test_admin_api import login


async def chat(session, channel, account, name, *msgs, ext=None):
    p = Profile(name=name)
    session.add(p)
    await session.flush()
    i = ChannelIdentity(channel=channel, external_id=ext or f"{channel}-{name}", account_id=account, profile_id=p.id)
    session.add(i)
    await session.flush()
    c = Conversation(channel_identity_id=i.id, profile_id=p.id)
    session.add(c)
    await session.flush()
    for role, text in msgs:
        session.add(Message(conversation_id=c.id, role=role, content_redacted=text))
    await session.commit()
    return c.id


async def test_inbox_filters_by_page_waiting_and_search_and_counts_everything(client, maker, session):
    session.add_all([ChannelConfig(channel="messenger", account_id="P1", label="Quantum Method"),
                     ChannelConfig(channel="messenger", account_id="P2", label="Quantum Lab"),
                     ChannelConfig(channel="whatsapp", account_id="W1", label="Helpline")])
    await session.commit()
    a = await chat(session, "messenger", "P1", "Rahim", ("user", "fee koto?"))
    b = await chat(session, "messenger", "P2", "Karim", ("user", "lab test"), ("assistant", "জি"))
    w = await chat(session, "whatsapp", "W1", "Salma", ("user", "blood lagbe"))
    await chat(session, "messenger", "P1", "Tester", ("user", "x"), ext="test:abc")   # Test Chat never shows
    staff = await login(client, maker, "agent", "agent@x.com")
    get = lambda **p: client.get("/api/conversations", params=p, headers=staff)
    rows = (await get()).json()
    assert {r["id"] for r in rows} == {a, b, w}
    by_id = {r["id"]: r for r in rows}
    assert (by_id[a]["account_label"], by_id[a]["waiting"], by_id[b]["waiting"]) == ("Quantum Method", True, False)
    assert [r["id"] for r in (await get(account_id="P2")).json()] == [b]
    assert {r["id"] for r in (await get(waiting=True)).json()} == {a, w}
    assert [r["id"] for r in (await get(q="blood")).json()] == [w]
    assert [r["id"] for r in (await get(q="karim")).json()] == [b]
    s = (await client.get("/api/conversations/summary", headers=staff)).json()
    assert (s["all"], s["waiting"], s["channels"]) == (3, 2, {"messenger": 2, "instagram": 0, "whatsapp": 1})
    assert {(x["label"], x["conversations"]) for x in s["accounts"]} == {("Quantum Method", 1), ("Quantum Lab", 1), ("Helpline", 1)}


async def test_ready_made_tests_are_added_once(client, maker):
    staff = await login(client, maker, "agent", "agent@x.com")
    r = (await client.post("/api/eval/cases/starter", headers=staff)).json()
    assert r["added"] > 20 and r["added"] <= r["available"]
    again = (await client.post("/api/eval/cases/starter", headers=staff)).json()
    assert again["added"] == 0
    cases = (await client.get("/api/eval/cases", headers=staff)).json()
    assert len(cases) == r["added"] and all("ready-made" in c["tags"] for c in cases)
