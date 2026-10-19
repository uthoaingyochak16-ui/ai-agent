import json

from sqlalchemy import select

from app.llm.service import LLMService
from app.models import FamilyMember, ProfileFact
from app.services import info_detect
from app.services.ingest import handle_message
from app.services.settings_service import set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM


def test_split_flags_only_pieces_that_may_hold_details():
    pieces = info_detect.split("amar nam Fahim, ami CUET e pori. course fee koto?")
    assert [(p.text, p.info, p.question) for p in pieces] == [
        ("amar nam Fahim", True, False), ("ami CUET e pori", True, False), ("course fee koto?", False, True)]
    for text in ("আমার মায়ের ডায়াবেটিস আছে", "my son is in class 5", "blood group O+", "boyosh 25", "01712345678"):
        assert info_detect.worth_checking(info_detect.split(text)), text
    for text in ("course fee koto?", "kobe shuru hobe?", "ok thanks", "hello"):
        assert not info_detect.worth_checking(info_detect.split(text)), text


async def setup(session, maker, reply):
    await seed_llm(session, routes={"extraction": ("anthropic", "ext", None, None)})
    fake = FakeLLM({"ext": reply if callable(reply) else json.dumps(reply)})
    return fake, LLMService(maker, fake)


async def send(session, llm, text, mid, uid="test:info"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def facts(session):
    return {f.key: f.value for f in (await session.execute(select(ProfileFact))).scalars() if not f.key.startswith("merge_")}


async def test_info_in_any_message_reaches_the_profile(session, maker):
    fake, llm = await setup(session, maker, {
        "facts": {"university": "CUET", "class_level": "3rd year"},
        "family": [{"relation": "mother", "age": 55, "details": {"health_conditions": "diabetes"}}],
        "pieces": [{"n": 1, "type": "guest_info"}, {"n": 2, "type": "guest_info"}, {"n": 3, "type": "question"}]})
    r = await send(session, llm, "hello", "a0")
    r.profile.consent_given = True
    await session.commit()
    r = await send(session, llm, "ami CUET e 3rd year e pori, amar ammur diabetes. course fee koto?", "a1")
    info = r.post["info"]
    assert info["checked"] and [p["type"] for p in info["pieces"]] == ["guest_info", "guest_info", "question"]
    assert (await facts(session))["university"] == "CUET"
    fam = (await session.execute(select(FamilyMember))).scalar_one()
    assert (fam.relation, fam.age, fam.details) == ("mother", 55, {"health_conditions": "diabetes"})
    sent = fake.calls[-1][1].messages[0]["content"]
    assert "PIECES:\n1* ami CUET e 3rd year e pori" in sent and "3 course fee koto?" in sent  # flagged / not flagged


async def test_a_plain_question_costs_no_ai_call(session, maker):
    fake, llm = await setup(session, maker, {})
    r = await send(session, llm, "course fee koto?", "b1")
    assert r.post["info"]["checked"] is False and fake.calls == []


async def test_a_bare_answer_is_read_with_the_question_before_it(session, maker):
    seen = {}

    def reply(req):
        seen["content"] = req.messages[0]["content"]
        return json.dumps({"age": 25, "pieces": [{"n": 1, "type": "guest_info"}]})
    fake, llm = await setup(session, maker, reply)
    r = await send(session, llm, "hello", "c0")
    r.profile.consent_given = True
    await session.commit()
    from app.models import Message
    session.add(Message(conversation_id=r.conversation.id, role="assistant", content_redacted="আপনার বয়স কত?"))
    await session.commit()
    r = await send(session, llm, "amar boyosh 25", "c1")
    assert "LAST ASSISTANT: আপনার বয়স কত?" in seen["content"]
    await session.refresh(r.profile)
    assert r.profile.age == 25


async def test_switched_off_falls_back_to_every_n_messages(session, maker):
    fake, llm = await setup(session, maker, {"name": "X"})
    await set_setting(session, "info_collection_enabled", False)
    await set_setting(session, "extraction_every_n_messages", 0)
    await session.commit()
    r = await send(session, llm, "amar nam Fahim", "d1")
    assert "info" not in (r.post or {}) and fake.calls == []
