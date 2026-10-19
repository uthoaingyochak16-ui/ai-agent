import json

from sqlalchemy import select

from app.llm.service import LLMService
from app.models import FamilyMember, Message, Profile, ProfileFact
from app.services import consent as C
from app.services import family
from app.services.identity import resolve
from app.services.merge import merge_profiles, unmerge_profiles
from app.services.post_reply import extract_profile
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login


def test_normalizers():
    assert [family.blood_group(x) for x in ("a+", "O -", "AB positive", "b-ve", "B+ve", "x+")] == ["A+", "O-", "AB+", "B-", "B+", None]
    assert [family.relation_of(x) for x in ("Mother", "ma", "abba", "Son", "")] == ["mother", "mother", "father", "son", None]


async def guest(session, uid="test:fam", consent=True):
    r = await resolve(session, mk_msg(uid=uid, text="x", mid=f"{uid}-0"))
    r.profile.consent_given = consent
    await session.commit()
    return r


async def extract(session, maker, r, reply: dict, said="ami class 10 e pori, amar blood group O+"):
    from app.models import LLMRoute
    if not (await session.execute(select(LLMRoute))).first():
        await seed_llm(session, routes={"extraction": ("anthropic", "ext", None, None)})
    session.add(Message(conversation_id=r.conversation.id, role="user", content_redacted=said))
    await session.commit()
    llm = LLMService(maker, FakeLLM({"ext": json.dumps(reply)}))
    return await extract_profile(session, llm, r.profile, r.conversation, "messenger", is_test=True)


async def members(session):
    return [family.as_dict(m) for m in (await session.execute(select(FamilyMember).order_by(FamilyMember.id))).scalars()]


async def test_own_details_and_relatives_are_kept_apart(session, maker):
    r = await guest(session)
    await extract(session, maker, r, {
        "name": None, "facts": {"class_level": "Class 10", "school": "Ideal School", "blood_group": "o positive"},
        "family": [{"relation": "mother", "name": "Rahima", "age": 52, "blood_group": "B+", "details": {"health_conditions": "diabetes"}},
                   {"relation": "brother", "name": "Rafi", "age": None, "blood_group": None, "details": {"university": "CUET", "class_level": "2nd year"}}]})
    facts = {f.key: f.value for f in (await session.execute(select(ProfileFact))).scalars()}
    assert facts["class_level"] == "Class 10" and facts["school"] == "Ideal School" and facts["blood_group"] == "O+"
    fam = await members(session)
    assert [(m["relation"], m["name"], m["age"], m["blood_group"]) for m in fam] == [("mother", "Rahima", 52, "B+"), ("brother", "Rafi", None, None)]
    assert fam[0]["details"] == {"health_conditions": "diabetes"} and fam[1]["details"]["university"] == "CUET"
    assert "diabetes" not in facts.values()                                    # the mother's problem is not the guest's


async def test_a_later_mention_updates_the_same_person(session, maker):
    r = await guest(session)
    await extract(session, maker, r, {"family": [{"relation": "mother", "name": "Rahima", "details": {"health_conditions": "diabetes"}}]})
    await extract(session, maker, r, {"family": [{"relation": "mother", "name": None, "age": 53, "details": {"problem": "knee pain"}}]}, said="mar boyosh 53")
    fam = await members(session)
    assert len(fam) == 1 and fam[0]["name"] == "Rahima" and fam[0]["age"] == 53
    assert fam[0]["details"] == {"health_conditions": "diabetes", "problem": "knee pain"}


async def test_two_sons_stay_two_people(session, maker):
    r = await guest(session)
    await extract(session, maker, r, {"family": [{"relation": "son", "name": "Rafi", "age": 8}, {"relation": "son", "name": "Sami", "age": 5}]})
    assert [(m["name"], m["age"]) for m in await members(session)] == [("Rafi", 8), ("Sami", 5)]


async def test_nothing_kept_without_consent_and_ai_never_overrides_staff(session, maker):
    r = await guest(session, consent=False)
    await extract(session, maker, r, {"family": [{"relation": "father", "name": "Karim", "age": 60}]})
    assert await members(session) == []
    r.profile.consent_given = True
    session.add(FamilyMember(profile_id=r.profile.id, relation="father", name="Abdul Karim", age=61, details={}, source="staff"))
    await session.commit()
    await extract(session, maker, r, {"family": [{"relation": "father", "name": "Karim", "age": 60, "blood_group": "A+"}]})
    fam = await members(session)
    assert len(fam) == 1 and (fam[0]["name"], fam[0]["age"], fam[0]["blood_group"]) == ("Abdul Karim", 61, "A+")  # gap filled only


async def test_erase_and_merge_carry_relatives(session):
    a, b = await guest(session, "test:a"), await guest(session, "test:b")
    await family.upsert(session, b.profile, {"relation": "sister", "name": "Mina"})
    log = await merge_profiles(session, keep_id=a.profile.id, merge_id=b.profile.id, actor="t", reason="t")
    assert (await members(session))[0]["name"] == "Mina" and (await session.execute(select(FamilyMember.profile_id))).scalar_one() == a.profile.id
    await unmerge_profiles(session, log.id)
    assert (await session.execute(select(FamilyMember.profile_id))).scalar_one() == b.profile.id
    await C.erase_profile(session, b.profile.id)
    assert await members(session) == []


async def test_staff_can_add_edit_and_remove_a_relative(client, maker):
    async with maker() as s:
        pid = (await guest(s)).profile.id
    h = await login(client, maker, role="agent", email="ag@x.com")
    r = await client.put(f"/api/profiles/{pid}/family", json={"relation": "Mother", "name": "Rahima", "age": 52, "blood_group": "b+",
                                                            "details": {"Health problems": "diabetes"}}, headers=h)
    assert r.status_code == 200 and r.json()["blood_group"] == "B+" and r.json()["details"] == {"health_problems": "diabetes"}
    fid = r.json()["id"]
    assert (await client.put(f"/api/profiles/{pid}/family/{fid}", json={"relation": "mother", "blood_group": "Z"}, headers=h)).status_code == 422
    d = (await client.get(f"/api/profiles/{pid}", headers=h)).json()
    assert [m["name"] for m in d["family"]] == ["Rahima"]
    assert (await client.delete(f"/api/profiles/{pid}/family/{fid}", headers=h)).status_code == 200
    assert (await client.get(f"/api/profiles/{pid}", headers=h)).json()["family"] == []


async def test_the_name_placeholder_never_overwrites_the_name(session, maker):
    r = await guest(session)
    r.profile.name = "রহিম উদ্দিন"
    await session.commit()
    for echoed in ("[NAME_1]", "NAME_1"):  # the model sees the guest's name hidden and may hand it back
        await extract(session, maker, r, {"name": echoed, "family": [{"relation": "mother", "name": echoed, "age": 50}]})
        await session.refresh(r.profile)
        assert r.profile.name == "রহিম উদ্দিন"
    assert [m["name"] for m in await members(session)] == [None]


async def test_a_relatives_name_never_replaces_the_guests(session, maker):
    r = await guest(session)
    r.profile.name = "Fahim"
    await session.commit()
    await extract(session, maker, r, {"name": "Rahima", "family": [{"relation": "mother", "name": "Rahima"}]},
                  said="amar ammur nam Rahima, kintu amar nam Fahim")
    await session.refresh(r.profile)
    assert r.profile.name == "Fahim" and [m["name"] for m in await members(session)] == ["Rahima"]
