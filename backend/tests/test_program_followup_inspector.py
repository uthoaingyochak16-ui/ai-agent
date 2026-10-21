"""Program replies (quoted names), the district follow-up, reply styles per question type and the reply inspector."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.llm.service import LLMService
from app.models import Message, Profile, Program, QAPair
from app.rag.districts import find_districts
from app.rag.embeddings import HashingEmbedder, embed_texts, embedding_signature
from app.rag.programs import quote_program_names, today_bd
from app.rag.templates import normalize_question
from app.services.ingest import handle_message
from app.services.pipeline import run_pipeline
from app.services.reply_style import classify
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api2 import admin, agent  # noqa: F401 - fixtures

EMB = (HashingEmbedder(1024), None)
TODAY = today_bd()
BATCH = "কোয়ান্টাম মেথড মেডিটেশন কোর্স — ব্যাচ ৪৯৯ZG"


def d(n):
    return TODAY + timedelta(days=n)


@pytest.fixture
async def progs(session, maker):
    session.add_all([Program(name=BATCH, start_date=d(10), end_date=d(13), fee="১১,৫০০ টাকা", place="আইডিইবি ভবন, কাকরাইল, ঢাকা"),
                     Program(name="Yoga Orientation Rajshahi", start_date=d(20), place="রাজশাহী সেন্টার, রাজশাহী")])
    await set_setting(session, "program_followup_enabled", True)
    await session.commit()
    return LLMService(maker, FakeLLM())  # no AI route: program answers come straight from the table


async def send(session, llm, text, mid, uid="test:prog"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def bot(session, uid="test:prog"):
    return [m.content_redacted for m in (await session.execute(
        select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def the_profile(session) -> Profile:
    p = (await session.execute(select(Profile))).scalars().first()
    await session.refresh(p)
    return p


# ---- program date format --------------------------------------------------------------------------------------------
async def test_program_date_reply_puts_the_name_in_quotes(session, progs):
    r = await run_pipeline(session, progs, text="মেডিটেশন কোর্স কবে?", profile=None, conversation=None, channel="messenger",
                           persist=False)
    assert r.path == "program"
    assert r.text.startswith(f'"{BATCH}" অনুষ্ঠিত হবে ')
    assert "ঢাকা" in r.text and "১১,৫০০" not in r.text                        # only what was asked; place from the table


async def test_an_ai_written_program_answer_gets_the_quotes_too(session, maker):
    session.add(Program(name=BATCH, start_date=d(10), end_date=d(13), place="ঢাকা"))
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await session.commit()
    fake = FakeLLM({"small": f"{BATCH} অনুষ্ঠিত হবে ২০–২৩ নভেম্বর, ঢাকা।"})
    r = await run_pipeline(session, LLMService(maker, fake), text="মেডিটেশন কোর্স কবে?", profile=None, conversation=None,
                           channel="messenger", persist=False)
    assert r.path == "llm" and r.text.startswith(f'"{BATCH}" অনুষ্ঠিত হবে')
    assert "Write a program's name exactly as given" in fake.calls[0][1].system


def test_quoting_never_doubles_quotes():
    p = Program(name="Yoga Orientation", start_date=TODAY)
    assert quote_program_names('"Yoga Orientation" is on Friday; Yoga Orientation again', [p]) == \
        '"Yoga Orientation" is on Friday; "Yoga Orientation" again'


# ---- district follow-up -----------------------------------------------------------------------------------------------
async def test_direct_answer_first_then_the_district_question_as_its_own_bubble(session, progs):
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "f1")
    msgs = await bot(session)
    assert len(msgs) == 2 and msgs[0].startswith(f'"{BATCH}"') and msgs[1] == DEFAULTS["program_district_prompt"]
    await send(session, progs, "yoga orientation kobe?", "f2")                   # another program question: not asked again
    assert DEFAULTS["program_district_prompt"] not in (await bot(session))[2:]


async def test_no_district_question_after_a_general_question(session, progs):
    await send(session, progs, "kemon achen?", "g1")
    await send(session, progs, "What is the capital of France?", "g2")
    assert DEFAULTS["program_district_prompt"] not in await bot(session)


async def test_a_known_location_is_never_asked_again(session, progs):
    await send(session, progs, "kemon achen?", "k0")
    p = await the_profile(session)
    p.location, p.consent_given = "Khulna", True                                  # no program in Khulna
    await session.commit()
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "k1")
    assert DEFAULTS["program_district_prompt"] not in await bot(session)
    assert len(await bot(session)) == 2                                           # small talk + the answer, nothing else


def assert_nearby(reply: str) -> None:
    """The one Rajshahi program, in the template: its name in quotes, its dates and place filled in."""
    head = DEFAULTS["program_nearby_reply"].split("{program_name}")[0]
    assert reply.startswith(head + '"Yoga Orientation Rajshahi"')
    assert "রাজশাহী সেন্টার, রাজশাহী।" in reply and "{" not in reply


async def test_a_known_district_with_a_verified_program_gets_the_suggestion(session, progs):
    await send(session, progs, "kemon achen?", "s0")
    p = await the_profile(session)
    p.location, p.consent_given = "রাজশাহী", True
    await session.commit()
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "s1")
    assert_nearby((await bot(session))[-1])


async def test_a_district_answer_saves_the_location_and_suggests_only_a_verified_program(session, progs):
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "d1")
    p = await the_profile(session)
    p.consent_given = True
    await session.commit()
    await send(session, progs, "ami Rajshahi te thaki", "d2")
    assert_nearby((await bot(session))[-1])
    assert (await the_profile(session)).location == "রাজশাহী"


async def test_a_district_without_a_program_gets_no_invented_nearest_place(session, progs):
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "u1")
    await send(session, progs, "Bandarban", "u2")                                 # a real district, no program there
    reply = (await bot(session))[-1]
    assert reply == DEFAULTS["program_nearby_none"]
    assert not {"ঢাকা", "রাজশাহী", "Chattogram", "চট্টগ্রাম"} & set(reply.replace("।", " ").split())


async def test_not_answering_the_district_question_is_fine(session, progs):
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "n1")
    await send(session, progs, "kemon achen?", "n2")                              # ignored: carry on, never ask again
    await send(session, progs, "yoga orientation kobe?", "n3")
    assert (await bot(session)).count(DEFAULTS["program_district_prompt"]) == 1
    assert DEFAULTS["program_nearby_none"] not in await bot(session)


def test_districts_are_matched_exactly_in_any_script():
    assert find_districts("ami Chittagong e thaki") == ["চট্টগ্রাম"]
    assert find_districts("আমি ঢাকায় থাকি") == ["ঢাকা"]
    assert find_districts("Bogra") == ["বগুড়া"] and find_districts("rajshahi theke") == ["রাজশাহী"]
    assert find_districts("Mirpur") == [] and find_districts("Narnia") == []      # not a district: nothing guessed


# ---- reply styles -----------------------------------------------------------------------------------------------------
async def test_reply_style_template_and_followup_per_question_type(session, progs):
    await set_setting(session, "reply_styles", {"program_date": {"template": "{answer}\n\nআর কিছু জানতে চান?",
                                                                 "followup": "রেজিস্ট্রেশনের জন্য লিংক চাইলে বলুন।"},
                                                "greeting": {"followup_en": "x"}})
    await set_setting(session, "program_followup_enabled", False)
    await session.commit()
    await send(session, progs, "মেডিটেশন কোর্স কবে?", "r1")
    msgs = await bot(session)
    assert msgs[0].endswith("আর কিছু জানতে চান?") and msgs[1] == "রেজিস্ট্রেশনের জন্য লিংক চাইলে বলুন।"


def test_question_types():
    assert classify("hi", "greeting") == "greeting"
    assert classify("course fee koto?", "program", {"program_match": {"wants_fee": True}}) == "program_fee"
    assert classify("program kothay hobe?", "program", {"program_match": {"wants_place": True}}) == "program_location"
    assert classify("ami bhorti hote chai", "llm") == "enrolment"
    assert classify("Quantum er office kothay?", "no_context") == "unknown"
    assert classify("What is the capital of France?", "llm") == "general"
    assert classify("কোয়ান্টাম ফাউন্ডেশন কী?", "llm") == "org_info"


async def test_admin_api_validates_reply_styles(client, admin):  # noqa: F811
    ok = await client.put("/api/ai/settings", headers=admin, json={"reply_styles": {"unknown": {"handover": "offer", "tone": "warm"}}})
    assert ok.status_code == 200
    bad = await client.put("/api/ai/settings", headers=admin, json={"reply_styles": {"unknown": {"template": "no placeholder"}}})
    assert bad.status_code == 422


# ---- reply inspector ----------------------------------------------------------------------------------------------------
async def test_inspector_shows_path_matched_context_and_errors(session, client, admin):  # noqa: F811
    q = "Head office er phone number koto?"
    session.add(QAPair(question=q, question_norm=normalize_question(q), answer_template="+88 01714 974333",
                       category="Contact & Address", embedding=(await embed_texts(session, [q]))[0],
                       embedding_version=await embedding_signature(session)))
    session.add(Program(name=BATCH, start_date=d(10), end_date=d(13), place="ঢাকা"))
    await session.commit()

    i = (await client.post("/api/test-chat", headers=admin, json={"message": q, "no_save": True})).json()["debug"]["inspector"]
    assert (i["path"], i["path_group"], i["final_reply"]) == ("qa_shortcut", "Q&A", "+88 01714 974333")
    assert i["matched"][0].startswith("Q&A #") and i["question_type"] == "org_info" and i["language"] == "banglish"

    i = (await client.post("/api/test-chat", headers=admin, json={"message": "মেডিটেশন কোর্স কবে?", "no_save": True})).json()["debug"]["inspector"]
    assert i["path_group"] == "program" and i["matched"] == [f'Program: "{BATCH}"'] and i["question_type"] == "program_date"

    # no AI model configured: the error and a plain-language reason are shown
    i = (await client.post("/api/test-chat", headers=admin, json={"message": "Can you explain photosynthesis simply?",
                                                                  "no_save": True})).json()["debug"]["inspector"]
    assert i["path"] in ("no_context", "llm_unavailable") and i["why"]
    assert any("matched" in w or "AI model could not be used" in w for w in i["why"])


async def test_inspector_explains_an_empty_reply_during_handover(session, client, admin):  # noqa: F811
    out = (await client.post("/api/test-chat", headers=admin, json={"message": "agent", "session_id": "insp1"})).json()
    assert out["debug"]["inspector"]["path_group"] == "handover"
    out = (await client.post("/api/test-chat", headers=admin, json={"message": "ar ekta prosno", "session_id": "insp1"})).json()
    i = out["debug"]["inspector"]
    assert i["final_reply"] == "" and "team member has taken over" in i["why"][0]


async def test_customer_replies_store_the_inspector_for_the_inbox(session, maker):
    q = "Head office er phone number koto?"
    session.add(QAPair(question=q, question_norm=normalize_question(q), answer_template="+88 01714 974333",
                       embedding=(await embed_texts(session, [q]))[0], embedding_version=await embedding_signature(session)))
    await session.commit()
    await handle_message(session, mk_msg(uid="real-guest", text=q, mid="c1"), llm=LLMService(maker, FakeLLM()))
    m = (await session.execute(select(Message).where(Message.role == "assistant"))).scalars().first()
    assert m.sources_json["inspector"]["path_group"] == "Q&A" and m.sources_json["inspector"]["why"]


async def test_several_nearby_programs_are_one_list_without_those_the_answer_named(session):
    from datetime import timedelta
    from app.models import Program
    from app.rag.programs import today_bd
    from app.services.program_followup import _suggestions
    day = today_bd() + timedelta(days=3)
    for name in ("Songhayon", "Mukto Alochona", "Yoga Orientation"):
        session.add(Program(name=name, start_date=day, place="কাকরাইল, ঢাকা"))
    await session.commit()
    from app.rag.districts import find_districts
    text = await _suggestions(session, find_districts("ঢাকা")[0], "bn", skip_text='"Songhayon" হবে ২১ অক্টোবর')
    lines = text.split("\n")
    assert lines[0] == "আপনার জেলায় সামনে আমাদের এই আয়োজনগুলো আছে:"
    assert [x.split(" — ")[0] for x in lines[1:]] == ['• "Mukto Alochona"', '• "Yoga Orientation"']
    assert "ঢাকা-এ" not in text and all(x.endswith("কাকরাইল, ঢাকা") for x in lines[1:])
