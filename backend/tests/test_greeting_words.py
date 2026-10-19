"""Every common greeting is recognised (alone, combined, with courtesy words) - and real questions are not."""
import pytest

from app.services.greeting import _opening, is_greeting
from app.services.greeting_words import GREETING_WORDS

GREETINGS = [
    "hi", "Hiii!!", "hello", "Hlw", "hey there", "Assalamu Alaikum", "assalamualaikum wa rahmatullahi wa barakatuh",
    "Aslm", "salam vai", "আসসালামু আলাইকুম", "আসসালামুয়ালাইকুম ভাই", "সালাম আপু", "নমস্কার", "Nomoskar dada", "আদাব",
    "Good morning sir", "gm", "শুভ সকাল", "সুপ্রভাত", "shuvo sondha", "hi, assalamu alaikum", "Salam, good morning",
    "hello Quantum team", "Dear Sir", "Respected Madam", "এই যে, শুনছেন", "hello everyone", "👋", "wassup",
    "Walaikum assalam", "hi bhai, salam",
]
NOT_GREETINGS = [
    "hi, course fee koto?", "hello, next program kobe?", "salam, ami blood dite chai", "good morning, office kokhon khole?",
    "কেমন আছেন", "thanks", "ok", "Eid Mubarak", "hela korben na", "আসসালামু আলাইকুম, কোর্স কবে?", "🙂",
]


@pytest.mark.parametrize("text", GREETINGS)
def test_greetings_are_recognised(text):
    assert is_greeting(text, GREETING_WORDS), text


@pytest.mark.parametrize("text", NOT_GREETINGS)
def test_questions_and_small_talk_are_not_greetings(text):
    assert not is_greeting(text, GREETING_WORDS), text


def test_hindu_and_neutral_greetings_are_answered_in_kind():
    assert _opening("নমস্কার দাদা") == ("নমস্কার", "Namaskar") and _opening("adab") == ("আদাব", "Adab")
    assert _opening("assalamu alaikum") is None and _opening("hi") is None


async def test_the_reply_opens_with_the_guests_own_greeting(session):
    from app.services.ingest import handle_message
    from app.models import Message
    from sqlalchemy import select
    from tests.conftest import mk_msg
    await handle_message(session, mk_msg(uid="test:nmskr", text="নমস্কার", mid="g1"))
    reply = (await session.execute(select(Message).where(Message.role == "assistant"))).scalars().first()
    assert reply.content_redacted.startswith("নমস্কার!") and "আসসালামু" not in reply.content_redacted


async def test_festival_wishes_get_a_formal_wish_back(session):
    from app.services.smalltalk import smalltalk_reply
    assert (await smalltalk_reply(session, "ঈদ মোবারক!")).startswith("আপনাকেও ঈদ মোবারক")
    assert (await smalltalk_reply(session, "Eid Mubarak!")).startswith("Eid Mubarak to you as well")   # English in, English out
    assert (await smalltalk_reply(session, "শুভ নববর্ষ")).startswith("আপনাকেও নববর্ষের")
    assert (await smalltalk_reply(session, "salam, eid mubarak")).startswith("Eid Mubarak to you")      # greeting + wish
    assert "🙂" not in (await smalltalk_reply(session, "ok"))
