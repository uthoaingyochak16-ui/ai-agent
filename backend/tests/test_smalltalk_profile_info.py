from sqlalchemy import select

from app.models import ChannelIdentity, Message, Profile, ProfileFact
from app.services import consent as C
from app.services.ingest import handle_message
from app.services.settings_service import DEFAULTS
from app.services.smalltalk import match
from tests.conftest import mk_msg
from tests.test_admin_api2 import admin  # noqa: F401 - fixture

ITEMS, WORDS = DEFAULTS["smalltalk"], DEFAULTS["greeting_words"]


def answer_for(phrase):
    return next(x["answer"] for x in ITEMS if phrase in x["questions"])


def test_smalltalk_matching():
    who, how, what = answer_for("who are you"), answer_for("how are you"), answer_for("what do you do")
    for t, a in (("Who are you?", who), ("who r u", who), ("tumi ke bhai", who), ("আপনি কে?", who),
                 ("How are you??", how), ("hi, how are you", how), ("Kemon achen apu", how), ("কেমন আছেন", how),
                 ("how are yuo", how),  # typo
                 ("Apnara ki niye kaj koren?", what), ("আপনারা কি নিয়ে কাজ করেন?", what), ("What is Quantum Method", what),
                 ("thank you!!", answer_for("thanks")), ("ধন্যবাদ", answer_for("thanks"))):
        assert match(t, ITEMS, WORDS) == a, t
    for t in ("how much is the course fee?", "what do you sell", "who are your teachers for the meditation course",
              "hi", "", "how are you different from other courses and what is the fee"):
        assert match(t, ITEMS, WORDS) is None, t


async def test_smalltalk_is_answered_without_the_ai(maker):
    async with maker() as s:
        await handle_message(s, mk_msg(uid="test:st", text="hi", mid="s1"))
        await handle_message(s, mk_msg(uid="test:st", text="Who are you?", mid="s2"))
        bot = (await s.execute(select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars().all()
        en = next(x["answer_en"] for x in ITEMS if "who are you" in x["questions"])  # asked in English -> English
        assert bot[-1].content_redacted == en and bot[-1].sources_json == {"path": "smalltalk"}


async def test_profile_list_shows_the_chat_name_and_photo(client, maker, admin):  # noqa: F811
    async with maker() as s:
        p = Profile()
        s.add(p)
        await s.flush()
        s.add(ChannelIdentity(channel="messenger", external_id="PSID1", profile_id=p.id, display_name="Rahim Uddin",
                              avatar_url="https://cdn.example/r.jpg"))
        await s.commit()
    items = (await client.get("/api/profiles", headers=admin)).json()["items"]
    assert items[0]["display_name"] == "Rahim Uddin" and items[0]["avatar_url"] == "https://cdn.example/r.jpg"
    assert (await client.get("/api/profiles", headers=admin, params={"q": "rahim"})).json()["total"] == 1
    d = (await client.get(f"/api/profiles/{items[0]['id']}", headers=admin)).json()
    assert d["display_name"] == "Rahim Uddin"


async def test_staff_adds_details_and_the_ai_does_not_overwrite_them(client, maker, admin):  # noqa: F811
    async with maker() as s:
        p = Profile()
        s.add(p)
        await s.commit()
        pid = p.id
    assert (await client.put(f"/api/profiles/{pid}/facts", headers=admin, json={"key": "district", "value": "Chattogram"})).status_code == 200
    assert (await client.put(f"/api/profiles/{pid}/facts", headers=admin, json={"key": "Blood Group", "value": "B+"})).json()["key"] == "blood_group"
    assert (await client.put(f"/api/profiles/{pid}/facts", headers=admin, json={"key": "notes", "value": "Prefers calls after 6pm"})).status_code == 200
    assert (await client.put(f"/api/profiles/{pid}/facts", headers=admin, json={"key": "phone", "value": "0171"})).status_code == 422
    assert (await client.put(f"/api/profiles/{pid}/facts", headers=admin, json={"key": "card", "value": "4111111111111111"})).status_code == 422

    d = (await client.get(f"/api/profiles/{pid}", headers=admin)).json()
    facts = {f["key"]: f for f in d["facts"]}
    assert facts["district"]["group"] == "contact" and facts["district"]["source"] == "staff"
    assert facts["blood_group"]["group"] == "basic" and facts["blood_group"]["label"] == "Blood group"
    assert "Prefers calls" not in (d["summary"] or "")  # staff notes are never in the AI's summary

    async with maker() as s:  # the AI extracts something else later: staff's value wins, notes are staff-only
        p = await s.get(Profile, pid)
        p.consent_given = True
        assert await C.apply_field(s, p, "district", "Dhaka") == "kept"
        assert await C.apply_field(s, p, "notes", "AI note") == "rejected"
        assert await C.apply_field(s, p, "interests", "meditation") == "saved"
        await s.commit()
    facts = {f["key"]: f for f in (await client.get(f"/api/profiles/{pid}", headers=admin)).json()["facts"]}
    assert facts["district"]["value"] == "Chattogram" and facts["interests"]["source"] == "ai"

    assert (await client.delete(f"/api/profiles/{pid}/facts/blood_group", headers=admin)).status_code == 200
    assert "blood_group" not in {f["key"] for f in (await client.get(f"/api/profiles/{pid}", headers=admin)).json()["facts"]}
    cat = (await client.get("/api/profiles/fields", headers=admin)).json()
    assert {"basic", "contact", "work", "family", "interests"} <= {g["id"] for g in cat["groups"]}


async def test_personal_catalog_details_wait_for_consent(session):
    p = Profile()
    session.add(p)
    await session.flush()
    assert await C.apply_field(session, p, "address", "House 5, Road 2") == "pending"   # identifies the person
    assert await C.apply_field(session, p, "interests", "yoga") == "saved"               # harmless preference
    await C.record_consent(session, p, True)
    keys = {f.key: f.value for f in (await session.execute(select(ProfileFact))).scalars()}
    assert keys["address"] == "House 5, Road 2" and keys["interests"] == "yoga"
