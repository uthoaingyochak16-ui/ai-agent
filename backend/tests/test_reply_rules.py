import json

import pytest
from sqlalchemy import select

from app.llm.service import LLMService
from app.models import Conversation, Message, QAPair
from app.privacy.guard import PrivacyGuard, PromptInputs
from app.rag.embeddings import HashingEmbedder
from app.rag.templates import normalize_question
from app.services.consent import is_erasure_request
from app.services.ingest import handle_message
from app.services.reply_split import split_reply, visual_lines
from app.services.settings_service import set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM


def test_prompt_names_one_reply_language():
    for lang, want in (("bn", "REPLY IN: Bangla (Bengali script, not Banglish)"), ("en", "REPLY IN: English")):
        req = PrivacyGuard().prepare(PromptInputs(question="x", reply_language=lang))
        assert want in req.messages[-1]["content"]
    assert "Never write Banglish" in PrivacyGuard().prepare(PromptInputs(question="x")).system


async def test_banglish_question_asks_for_a_bangla_answer(session, maker):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "retrieval_min_similarity", 0.0)
    await session.commit()
    fake = FakeLLM({"small": "ok"})
    await handle_message(session, mk_msg(uid="test:lang1", text="course fee koto?", mid="l1"), llm=LLMService(maker, fake))
    sent = [req for model, req in fake.calls if model == "small"]
    assert not sent or "REPLY IN: Bangla" in sent[-1].messages[-1]["content"]


def test_split_keeps_short_replies_whole_and_cuts_long_ones():
    short = "লাইন ১\nলাইন ২\nলাইন ৩"
    assert split_reply(short, 5) == [short]
    long = "\n\n".join(f"অনুচ্ছেদ {i}: " + "কোয়ান্টাম মেথড কোর্সে মেডিটেশন শেখানো হয়। " * 2 for i in range(6))
    parts = split_reply(long, 5)
    assert len(parts) > 1 and all(visual_lines(p) <= 5 for p in parts)
    assert "".join(p.replace("\n", "").replace(" ", "") for p in parts) == long.replace("\n", "").replace(" ", "")
    assert split_reply(long, 0) == [long]


async def test_long_answers_go_as_several_messages(session, maker):
    session.add(QAPair(question="kobe course", question_norm=normalize_question("kobe course"),
                       answer_template="\n".join(f"{i}. ঢাকা কোর্স: ১৬-১৯ অক্টোবর, ভেন্যু কোয়ান্টাম সেন্টার, শান্তিনগর" for i in range(1, 9)),
                       embedding=(await HashingEmbedder(1024).embed(["kobe course"]))[0], embedding_version="hash:hash-ngram-v1:1024"))
    await session.commit()
    await handle_message(session, mk_msg(uid="test:split", text="kobe course", mid="s1"), llm=LLMService(maker, FakeLLM()))
    bot = (await session.execute(select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars().all()
    assert len(bot) >= 2 and all(visual_lines(m.content_redacted) <= 5 for m in bot)
    assert bot[-1].sources_json and not bot[0].sources_json        # the debug path rides on the last part


async def test_a_saved_answer_that_promises_a_person_hands_over(session, maker):
    session.add(QAPair(question="amar ovijog ache", question_norm=normalize_question("amar ovijog ache"),
                       answer_template="দুঃখিত। আমি এখনই একজন প্রতিনিধিকে জানাচ্ছি।",
                       embedding=(await HashingEmbedder(1024).embed(["amar ovijog ache"]))[0], embedding_version="hash:hash-ngram-v1:1024"))
    await session.commit()
    await handle_message(session, mk_msg(uid="test:ho", text="amar ovijog ache", mid="h1"), llm=LLMService(maker, FakeLLM()))
    conv = (await session.execute(select(Conversation))).scalar_one()
    assert conv.ai_paused and conv.status == "handover" and conv.handover_reason.startswith("qa:")


@pytest.mark.parametrize("text", ["Amar data muche din", "amar sob tottho delete kore din", "my data delete please"])
def test_banglish_erasure_requests(text):
    assert is_erasure_request(text)


def test_not_erasure():
    assert not is_erasure_request("amar data ki safe?")


def test_stored_placeholders_in_history_never_reach_the_model_as_placeholders():
    req = PrivacyGuard().prepare(PromptInputs(
        question="How can I donate blood?", chunks=("Blood lab: call 01714974333",),
        recent_messages=(("user", "rokto dan kothay?"), ("assistant", "রক্তদান: মোবাইল [PHONE_1], [PHONE_2]"))))
    history = req.messages[1]["content"]
    assert "[PHONE_" not in history and "[…]" in history                     # the old marks are neutral now
    assert "[PHONE_1]" in req.messages[-1]["content"]                          # the context's number is the one to copy
    assert req.redactor.restore("call [PHONE_1]") == "call 01714974333"


def test_our_public_numbers_stay_readable_in_storage_but_guests_do_not():
    from app.privacy.redaction import redact_for_storage
    keep = frozenset({"+8801714974333", "blood@quantummethod.org.bd"})
    out = redact_for_storage("Office 01714-974333, blood@quantummethod.org.bd; guest 01812345678, a@b.com", keep=keep)
    assert "01714-974333" in out and "blood@quantummethod.org.bd" in out
    assert "01812345678" not in out and "a@b.com" not in out


async def test_a_stored_reply_keeps_the_office_number(session, maker, tmp_path):
    from app.models import Document
    from app.rag.documents import LocalStorage, process_document
    st = LocalStorage(str(tmp_path))
    d = Document(title="Office", file_path=st.save("o.txt", "Quantum office phone 01714-974333.".encode()), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=(HashingEmbedder(1024), None))
    session.add(QAPair(question="office number", question_norm=normalize_question("office number"),
                       answer_template="Call our office: 01714-974333",
                       embedding=(await HashingEmbedder(1024).embed(["office number"]))[0], embedding_version="hash:hash-ngram-v1:1024"))
    await session.commit()
    await handle_message(session, mk_msg(uid="test:pub", text="office number", mid="p1"), llm=LLMService(maker, FakeLLM()))
    await handle_message(session, mk_msg(uid="test:pub", text="amar number 01812345678", mid="p2"), llm=LLMService(maker, FakeLLM()))
    rows = (await session.execute(select(Message).order_by(Message.id))).scalars().all()
    assert any("01714-974333" in m.content_redacted for m in rows if m.role == "assistant")   # ours: readable
    assert all("01812345678" not in m.content_redacted for m in rows)                          # the guest's: hidden
