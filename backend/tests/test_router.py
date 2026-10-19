import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.llm.providers import LLMError
from app.llm.service import LLMService
from app.models import Conversation, Message, Profile, QAPair
from app.rag.embeddings import HashingEmbedder
from app.services.ingest import handle_message
from app.services.router import parse_decision
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM

ROUTES = {"router": ("anthropic", "router-m", None, None), "answer": ("anthropic", "small", None, None),
          "rewrite": ("anthropic", "rewrite-m", None, None)}


def decision(intent, *, language="bn", needs_human=False, standalone=None):
    return json.dumps({"intent": intent, "language": language, "needs_human": needs_human, "standalone_question": standalone})


@pytest.fixture
async def env(session, maker):
    await seed_llm(session, routes=ROUTES)
    await set_setting(session, "router_enabled", True)
    await session.commit()
    fake = FakeLLM({"router-m": decision("question"), "small": "an answer", "rewrite-m": "rewritten"})
    return type("Env", (), {"fake": fake, "llm": LLMService(maker, fake), "session": session})()


async def say(env, text, mid, uid="test:router"):
    return await handle_message(env.session, mk_msg(uid=uid, text=text, mid=mid), llm=env.llm)


async def bot_texts(session):
    return [m.content_redacted for m in (await session.execute(
        select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


def router_calls(env):
    return [req for model, req in env.fake.calls if model == "router-m"]


def test_parse_decision_is_strict():
    assert parse_decision(decision("complaint", needs_human=True)).needs_human is True
    assert parse_decision('```json\n{"intent": "greeting", "language": "en"}\n```').intent == "greeting"
    assert parse_decision('{"intent": "delete_everything"}') is None   # unknown intent
    assert parse_decision("not json") is None
    assert parse_decision('{"intent": "question", "language": "fr", "needs_human": "yes"}').needs_human is False


def test_parse_decision_keeps_what_the_guest_wants_only_for_questions():
    d = parse_decision('{"intent": "question", "request": "  Date of the next course in Dhaka "}')
    assert d.request == "Date of the next course in Dhaka" and d.as_debug()["request"] == d.request
    assert parse_decision('{"intent": "greeting", "request": "Say hello"}').request is None
    assert parse_decision('{"intent": "question", "request": 5}').request is None


async def test_greeting_the_word_list_does_not_know(env):
    env.fake.script["router-m"] = decision("greeting")
    r = await say(env, "শুভেচ্ছা নিবেন", "g1")
    assert (await bot_texts(env.session)) == [DEFAULTS["greeting_first_reply"]]
    assert "router: greeting" in r.events


async def test_complaint_and_needs_human_hand_over(env):
    env.fake.script["router-m"] = decision("complaint", language="en")
    r = await say(env, "your staff was rude to me yesterday", "c1")
    assert r.conversation.ai_paused and r.conversation.handover_reason == "router:complaint"
    assert (await bot_texts(env.session))[-1] == DEFAULTS["handover_ack_en"]

    env.fake.script["router-m"] = decision("question", needs_human=True)
    r = await say(env, "বুকে ব্যথা করছে এখন কী করব", "c2", uid="test:router2")
    assert r.conversation.ai_paused and (await bot_texts(env.session))[-1] == DEFAULTS["handover_ack"]


async def test_router_erasure_needs_an_explicit_yes(env):
    env.fake.script["router-m"] = decision("erasure_request", language="en")
    r = await say(env, "please forget everything about me", "e1")
    p = await env.session.get(Profile, r.profile.id)
    assert p.erased_at is None and r.conversation.erasure_requested_at is not None   # asked, not erased
    assert (await bot_texts(env.session))[-1] == DEFAULTS["erasure_confirm_prompt_en"]

    env.fake.script["router-m"] = decision("consent_yes")  # even a confused router can't erase: the word list decides
    r = await say(env, "no wait, keep it", "e2")
    await env.session.refresh(p)
    assert p.erased_at is None and r.conversation.erasure_requested_at is None        # anything but yes cancels

    env.fake.script["router-m"] = decision("erasure_request", language="en")
    await say(env, "please forget everything about me", "e3")
    r = await say(env, "YES", "e4")
    await env.session.refresh(p)
    assert p.erased_at is not None and (await bot_texts(env.session))[-1] == DEFAULTS["erasure_ack_en"]


async def test_an_old_erasure_question_is_not_confirmed_later(env):
    env.fake.script["router-m"] = decision("erasure_request")
    r = await say(env, "আমার সব কিছু ভুলে যান", "o1")
    conv = await env.session.get(Conversation, r.conversation.id)
    conv.erasure_requested_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await env.session.commit()
    env.fake.script["router-m"] = decision("question")
    await say(env, "হ্যাঁ", "o2")
    assert (await env.session.get(Profile, r.profile.id)).erased_at is None


async def test_consent_from_the_router_only_when_it_was_asked(env):
    env.fake.script["router-m"] = decision("consent_yes")
    r = await say(env, "accha save kore rakhen", "k1")          # nothing was asked: just a message
    assert (await env.session.get(Profile, r.profile.id)).consent_given is False

    p = await env.session.get(Profile, r.profile.id)
    p.consent_asked_at = datetime.now(timezone.utc)
    await env.session.commit()
    await say(env, "accha save kore rakhen", "k2")               # too long for the yes/no word list
    await env.session.refresh(p)
    assert p.consent_given is True and (await bot_texts(env.session))[-1] == DEFAULTS["consent_yes_ack"]


async def test_standalone_question_replaces_the_rewrite_call(env):
    env.fake.script["router-m"] = decision("question", standalone="কোয়ান্টাম মেথড কোর্সের ফি কত?")
    await say(env, "hello", "s0")                                # history so the question counts as a follow-up
    r = await say(env, "oita koto?", "s1")
    assert r.debug["question_rewrite"]["rewritten"] == "কোয়ান্টাম মেথড কোর্সের ফি কত?"
    assert r.debug["router"]["intent"] == "question"
    assert "rewrite-m" not in [m for m, _ in env.fake.calls]      # one call instead of two


async def test_router_failure_or_garbage_falls_back_to_the_rules(env):
    env.fake.script["router-m"] = LLMError("provider down", status=503)
    r = await say(env, "course er schedule ta bolben", "f1")
    assert not any(e.startswith("router:") for e in r.events) and (await bot_texts(env.session))  # still answered
    env.fake.script["router-m"] = "I think the user is asking about fees"
    r = await say(env, "ar weekend batch ache?", "f2")
    assert not any(e.startswith("router:") for e in r.events)


async def test_free_answers_and_the_switch_skip_the_router(env):
    vec = (await HashingEmbedder(1024).embed(["x"]))[0]
    env.session.add(QAPair(question="Course fee koto?", question_norm="course fee koto", answer_template="11500 টাকা", embedding=vec))
    await env.session.commit()
    await say(env, "Course fee koto?", "q1")                      # saved Q&A: 0 tokens
    await say(env, "hi", "q2")                                    # greeting list: 0 tokens
    assert router_calls(env) == []
    await set_setting(env.session, "router_enabled", False)
    await env.session.commit()
    await say(env, "something the lists do not know", "q3")
    assert router_calls(env) == []


async def test_router_request_goes_through_the_privacy_guard(env):
    await say(env, "amar number 01712345678, apnara call diben?", "p1")
    sent = json.dumps([m["content"] for m in router_calls(env)[0].messages], ensure_ascii=False)
    assert "01712345678" not in sent and "[PHONE_1]" in sent
