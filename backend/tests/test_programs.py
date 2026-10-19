import json
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.llm.providers import LLMError
from app.llm.service import LLMService
from app.models import Program, SemanticCache
from app.rag.programs import format_dates, purge_expired, today_bd, upcoming
from app.services.pipeline import run_pipeline
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api2 import admin  # noqa: F401 - fixture

TODAY = today_bd()


def d(n: int) -> date:
    return TODAY + timedelta(days=n)


@pytest.fixture
async def env(session, maker):
    """Two upcoming programs, entered out of order, plus one that ended yesterday. No AI model unless a test adds one."""
    session.add_all([
        Program(name="Yoga Orientation", start_date=d(30), end_date=d(31), fee="৩,০০০ টাকা", place="YMCA, কাকরাইল"),
        Program(name="Quantum Method Course", start_date=d(10), end_date=d(13), fee="১১,৫০০ টাকা", place="ঢাকা"),
        Program(name="Old Batch", start_date=d(-5), end_date=d(-1), fee="1", place="x"),
    ])
    await session.commit()
    fake = FakeLLM({"small": "AI analysis of the programs"})
    return type("Env", (), {"fake": fake, "llm": LLMService(maker, fake), "session": session})()


async def ask(env, text, *, llm=True, **kw):
    return await run_pipeline(env.session, env.llm if llm else LLMService(env.session.bind, FakeLLM()), text=text, profile=None,
                              conversation=None, channel="messenger", persist=kw.pop("persist", False), **kw)


def test_date_formats():
    assert format_dates(date(2026, 11, 20), date(2026, 11, 23), "bn") == "২০–২৩ নভেম্বর ২০২৬"
    assert format_dates(date(2026, 11, 20), date(2026, 11, 23), "en") == "20–23 November 2026"
    assert format_dates(date(2026, 10, 30), date(2026, 11, 2), "en") == "30 October – 2 November 2026"
    assert format_dates(date(2026, 12, 5), None, "bn") == "৫ ডিসেম্বর ২০২৬"


async def test_nearest_first_and_finished_programs_are_deleted(env):
    assert [p.name for p in await upcoming(env.session)] == ["Quantum Method Course", "Yoga Orientation"]   # Old Batch already over
    assert await purge_expired(env.session) == 1
    await env.session.commit()
    assert (await env.session.execute(select(func.count()).select_from(Program))).scalar_one() == 2
    # a program is kept through its last day, and removed the day after
    env.session.add(Program(name="Ends today", start_date=d(-2), end_date=d(0)))
    await env.session.commit()
    assert await purge_expired(env.session) == 0
    assert await purge_expired(env.session, d(1)) == 1


async def test_answers_come_from_the_table_without_an_ai_model(env):
    r = await ask(env, "পরবর্তী কোর্স কবে?", llm=False)
    assert r.path == "program" and "Quantum Method Course" in r.text
    assert format_dates(d(10), d(13), "bn") in r.text and "আর ১০ দিন বাকি" in r.text

    r = await ask(env, "course fee koto?", llm=False)
    assert r.path == "program" and "১১,৫০০ টাকা" in r.text and "Yoga" not in r.text

    r = await ask(env, "yoga class kobe?", llm=False)
    assert "Yoga Orientation" in r.text and "Quantum Method Course" not in r.text

    r = await ask(env, "Dhaka course kobe?", llm=False)               # Latin district name matches the Bangla place
    assert "Quantum Method Course" in r.text

    r = await ask(env, "What is the course fee?", llm=False)           # English question -> English answer
    assert r.text.startswith('"Quantum Method Course" — fee: ')         # the name always in quotation marks

    r = await ask(env, "next program kobe?", llm=False)                # "next" = one precise answer: the nearest
    assert "Quantum Method Course" in r.text and "Yoga Orientation" not in r.text
    r = await ask(env, "upcoming programs kobe?", llm=False)           # a general question lists them, nearest first
    assert r.text.index("Quantum Method Course") < r.text.index("Yoga Orientation") and "Old Batch" not in r.text


async def test_not_every_course_question_is_a_program_question(env):
    for q in ("How long is the course?", "মেডিটেশন কেন করবো?", "Meditation course fee koto?"):   # no date/fee/place ask, or an unknown subject
        r = await ask(env, q, llm=False)
        assert r.path != "program", q
    await env.session.execute(Program.__table__.delete().where(Program.name == "Yoga Orientation"))
    await env.session.commit()
    assert (await ask(env, "Yoga course fee koto?", llm=False)).path != "program"    # only the Quantum course row remains


async def test_with_an_ai_model_the_schedule_is_context_and_nothing_is_cached(env, maker):
    await seed_llm(env.session, routes={"answer": ("anthropic", "small", None, None)})
    env.fake.script["small"] = "Quantum Method Course অনুষ্ঠিত হবে ১০ দিন পরে।"
    r = await ask(env, "Next course kobe?", persist=True)
    assert r.path == "llm" and '"Quantum Method Course"' in r.text                    # the matched name is still quoted
    sent = json.dumps([m for _, req in env.fake.calls for m in req.messages], ensure_ascii=False)
    assert sent.index("PROGRAM Quantum Method Course") < sent.index("PROGRAM Yoga Orientation") and "Old Batch" not in sent
    assert "PROGRAMS: lines starting" in env.fake.calls[0][1].system
    assert (await env.session.execute(select(func.count()).select_from(SemanticCache))).scalar_one() == 0


async def test_every_answer_sees_the_schedule_so_a_follow_up_can_use_it(env):
    await seed_llm(env.session, routes={"answer": ("anthropic", "small", None, None)})
    r = await ask(env, "kothay hobe?", history=[("user", "yoga orientation ache?"), ("assistant", "Yes, Yoga Orientation is coming.")])
    sent = json.dumps([m for _, req in env.fake.calls for m in req.messages], ensure_ascii=False)
    assert r.path == "llm" and "PROGRAM Yoga Orientation" in sent and "YMCA" in sent


async def test_ai_failure_falls_back_to_the_table_and_handover_mode_skips_the_ai(env):
    await seed_llm(env.session, routes={"answer": ("anthropic", "small", None, None)})
    env.fake.script["small"] = LLMError("provider down", status=503)
    r = await ask(env, "course fee koto?")
    assert r.path == "program" and "১১,৫০০ টাকা" in r.text
    env.fake.calls.clear()
    r = await ask(env, "course fee koto?", knowledge_only=True)
    assert r.path == "program" and env.fake.calls == []


async def test_programs_api_validation_edit_delete_and_order(client, admin, session):  # noqa: F811
    j = lambda **kw: {"name": "Course A", "start_date": d(20).isoformat(), **kw}  # noqa: E731
    assert (await client.post("/api/programs", headers=admin, json=j(start_date=d(-3).isoformat()))).status_code == 422   # passed
    assert (await client.post("/api/programs", headers=admin, json=j(end_date=d(19).isoformat()))).status_code == 422      # ends before it starts
    a = (await client.post("/api/programs", headers=admin, json=j(fee="500", place="Dhaka"))).json()
    b = (await client.post("/api/programs", headers=admin, json={"name": "Course B", "start_date": d(5).isoformat()})).json()
    assert a["days_left"] == 20 and b["days_left"] == 5
    listing = (await client.get("/api/programs", headers=admin)).json()
    assert [p["name"] for p in listing["items"]] == ["Course B", "Course A"]       # nearest first
    upd = await client.put(f"/api/programs/{a['id']}", headers=admin, json=j(fee="600", place="Khulna", notes="bring ID"))
    assert upd.json()["fee"] == "600" and upd.json()["place"] == "Khulna"
    assert (await client.delete(f"/api/programs/{b['id']}", headers=admin)).status_code == 200
    session.add(Program(name="Gone", start_date=d(-4), end_date=d(-2)))             # finished: the list call deletes it
    await session.commit()
    assert [p["name"] for p in (await client.get("/api/programs", headers=admin)).json()["items"]] == ["Course A"]


async def test_a_long_program_name_is_found_by_its_distinctive_word(session):
    session.add_all([Program(name="Quantum Method Advanced Yoga Orientation Batch 12", start_date=d(7), fee="৩,০০০ টাকা", place="ঢাকা"),
                     Program(name="Quantum Method Weekend Course Batch 499 ZF", start_date=d(9), fee="১১,৫০০ টাকা")])
    await session.commit()
    ask_ = lambda q: run_pipeline(session, LLMService(session.bind, FakeLLM()), text=q, profile=None, conversation=None,  # noqa: E731
                                  channel="messenger", persist=False)
    r = await ask_("yoga fee koto?")
    assert r.path == "program" and "Yoga" in r.text and "Weekend" not in r.text and "৩,০০০" in r.text
    r = await ask_("course fee koto?")                 # "course" is one generic word of a long name: left to the knowledge base
    assert r.path != "program"


async def test_banglish_program_name_is_matched_and_uses_the_table(env):
    env.session.add(Program(name="প্রজ্ঞা জালালি", start_date=d(4), place="Dhaka"))
    await env.session.commit()
    r = await ask(env, "Next proga jalali program date kobe?", llm=False)
    assert r.path == "program" and '"প্রজ্ঞা জালালি"' in r.text


async def test_payment_actions_are_disabled_but_verified_program_fee_is_allowed(env):
    blocked = await ask(env, "Payment method ki?", llm=False)
    assert blocked.path == "payment_disabled"
    fee = await ask(env, "course fee koto?", llm=False)
    assert fee.path == "program" and "১১,৫০০" in fee.text
