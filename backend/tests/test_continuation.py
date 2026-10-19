"""A short reply to the assistant's own question continues that conversation (never small talk / a fresh question),
in the guest's language; and rules taught in Test Chat reach the answer prompt."""
from sqlalchemy import select

from app.models import Message
from app.services.continuation import reply_to_our_question
from app.services.language import detect_in_conversation
from app.services.settings_service import set_setting
from app.services import team_instructions as TI
from tests.test_lead_capture import env, send  # noqa: F401 - fixture

ASKED = [("user", "Quantum method course ki?"),
         ("assistant", "কোয়ান্টাম মেথড চার দিনের কোর্স। আপনি কি এই কোর্সটি করতে আগ্রহী?")]


def test_a_short_answer_to_our_question_is_recognised():
    assert reply_to_our_question("ji", ASKED) == "আপনি কি এই কোর্সটি করতে আগ্রহী?"
    assert reply_to_our_question("hya, dhakay", ASKED) and reply_to_our_question("Ok", ASKED)
    assert reply_to_our_question("Dhakay next course kobe?", ASKED) is None        # a new question of its own
    assert reply_to_our_question("ji", ASKED[:1]) is None                          # we asked nothing
    assert reply_to_our_question("ji", [("assistant", "কোর্সটি চার দিনের।")]) is None
    assert reply_to_our_question("ami ekhon office theke ber hoye bashay jacchi bhai", ASKED) is None  # too long


def test_a_short_reply_keeps_the_conversation_language():
    assert detect_in_conversation("Ok", ASKED) == "bn"       # the guest wrote Banglish before
    assert detect_in_conversation("Ok", [("user", "What does the course teach you?")]) == "en"
    assert detect_in_conversation("ji", []) == "bn"


async def test_ok_after_our_question_continues_instead_of_small_talk(session, env):  # noqa: F811
    fake, llm = await env()
    await set_setting(session, "lead_capture_enabled", False)
    await session.commit()
    fake.script["small"] = "কোয়ান্টাম মেথড চার দিনের কোর্স। আপনি কি এই কোর্সটি করতে আগ্রহী?"
    await send(session, llm, "How long is the meditation course?", "c1", uid="test:cont")
    fake.script["small"] = "রেজিস্ট্রেশন করতে ফর্মটি পূরণ করুন। আপনি কোন শহরে আছেন?"
    await send(session, llm, "Ok", "c2", uid="test:cont")
    last = (await session.execute(select(Message).where(Message.role == "assistant").order_by(Message.id.desc()))).scalars().first()
    assert last.content_redacted.startswith("রেজিস্ট্রেশন")                      # the AI answered, not "Sure 🙂"
    req = fake.calls[-1][1].audit_json()
    assert "CONTINUE:" in req and "আগ্রহী?" in req                               # it saw our question and the rule


async def test_taught_rules_reach_the_answer_prompt_and_can_be_switched_off(session, env):  # noqa: F811
    fake, llm = await env()
    await set_setting(session, "lead_capture_enabled", False)
    await TI.save(session, [{"text": "Always end with the hotline 16263.", "enabled": True},
                            {"text": "Never mention prices.", "enabled": False}])
    await session.commit()
    await send(session, llm, "How long is the meditation course?", "t1", uid="test:rules")
    req = fake.calls[-1][1].audit_json()
    assert "TEAM INSTRUCTIONS" in req and "hotline 16263" in req and "Never mention prices" not in req
