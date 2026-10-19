from sqlalchemy import select

from app.llm.service import LLMService
from app.models import Conversation, Message
from app.privacy.redaction import Redactor, redact_for_storage
from app.services import monitoring
from app.services.ingest import handle_message
from app.services.security import asks_for_card, contains_card
from app.services.settings_service import DEFAULTS
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login
from tests.test_lead_capture import env  # noqa: F401 - fixture


def test_asks_for_card():
    for text in ("Please send your card number to confirm.", "আপনার কার্ড নম্বর ও পিন দিন", "What is the CVV on your card?",
                 "Share the OTP you received"):
        assert asks_for_card(text), text
    for text in (DEFAULTS["card_warning"], DEFAULTS["card_warning_en"], "The course fee is 5000 taka, paid at the branch."):
        assert not asks_for_card(text), text


def test_a_phone_number_is_never_a_card():
    assert contains_card("my card 4111 1111 1111 1111")
    for phone in ("8801812345604", "+8801812345604", "01812345604"):          # 880... passes the card checksum
        assert not contains_card(phone)
        assert "[CARD_" not in Redactor().redact(phone) and "[PHONE_1]" in Redactor().redact(phone)
    assert redact_for_storage("4111111111111111") == "[CARD_1]"


async def bot(session):
    return (await session.execute(select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars().all()


async def test_guest_card_number_is_hidden_flagged_warned_and_alerted(session, maker):
    await seed_llm(session)
    await handle_message(session, mk_msg(uid="test:card", text="my card is 4111 1111 1111 1111", mid="c1"),
                         llm=LLMService(maker, FakeLLM()))
    user = (await session.execute(select(Message).where(Message.role == "user"))).scalar_one()
    assert "4111" not in user.content_redacted and user.flag == "card_shared"
    assert (await bot(session))[-1].content_redacted.startswith(DEFAULTS["card_warning_en"])
    assert (await monitoring.system_health(session))["security_24h"] == {"card_shared": 1, "card_request_blocked": 0, "state": "red"}
    assert "card_shared" in [a.key for a in await monitoring.check_alerts(session)]


async def test_ai_reply_asking_for_card_is_replaced(session, env):  # noqa: F811
    fake, llm = await env()
    from app.services.settings_service import set_setting
    await set_setting(session, "lead_capture_enabled", False)
    await session.commit()
    fake.script["small"] = "To book the meditation course please send your card number and CVV."
    await handle_message(session, mk_msg(uid="test:card2", text="How do I book the meditation course?", mid="d1"), llm=llm)
    last = (await bot(session))[-1]
    assert last.content_redacted == DEFAULTS["card_warning_en"] and last.flag == "card_request"
    assert "card_request" in [a.key for a in await monitoring.check_alerts(session)]


async def test_staff_cannot_ask_for_card(client, maker):
    async with maker() as s:
        await handle_message(s, mk_msg(uid="test:card3", text="hello", mid="e1"))
        conv_id = (await s.execute(select(Conversation.id))).scalar_one()
    h = await login(client, maker, role="agent", email="ag@x.com")
    r = await client.post(f"/api/conversations/{conv_id}/reply", json={"text": "আপনার কার্ড নম্বর দিন"}, headers=h)
    assert r.status_code == 422


def test_otp_or_pin_in_guidance_is_not_a_request():
    for text in ("রেজিস্ট্রেশন ফর্মে:\n- ব্যাচ বেছে নিতে হবে\n- মোবাইলে OTP যাচাই করতে হবে\n- ফি পরিশোধ করতে হবে",
                 "Fill in the form, verify your mobile number with an OTP, and pay the fee.",
                 "কোর্সের মেয়াদ চার দিন।", "ফর্মে ওটিপি যাচাই করে ফি পরিশোধ করুন।"):
        assert not asks_for_card(text), text
    for text in ("আপনার মোবাইলে আসা OTP টি আমাদের পাঠান।", "Please tell us the PIN.", "OTP ta amake din",
                 "কার্ডের মেয়াদ জানান।"):
        assert asks_for_card(text), text


def test_a_dont_in_any_bangla_verb_form_is_a_warning_not_a_request():
    for text in ("পাসওয়ার্ড বা কার্ডের তথ্য লিখবেন না।", "কার্ড নম্বর বা OTP কাউকে পাঠাবেন না।", "Please do not write passwords or card details."):
        assert not asks_for_card(text), text
