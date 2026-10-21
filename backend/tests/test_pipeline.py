import json
import time

import pytest
from sqlalchemy import func, select

from app.llm.service import LLMService
from app.models import (Chunk, Conversation, Document, LLMCall, LLMRoute, Message, Profile, QAPair, SemanticCache,
                        UnansweredQuestion, Variable)
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.rag.templates import normalize_question
from app.services import consent as C
from app.services.identity import resolve
from app.services.pipeline import run_pipeline
from app.services.post_reply import run_post_reply
from app.services.settings_service import set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM

EMB = (HashingEmbedder(1024), None)
ROUTES = {t: ("anthropic", m, None, None) for t, m in
          (("answer", "small"), ("answer_large", "big"), ("extraction", "small"), ("summarization", "small"))}
PRICES = {("anthropic", "small"): (1, 5), ("anthropic", "big"): (3, 15)}
HOURS = "Our shop is open from 9 am to 5 pm, Saturday to Thursday. We are closed on Friday."
SHIP = "Delivery inside Dhaka takes one to two days and costs 60 taka. Outside Dhaka it takes three to five days."


@pytest.fixture
async def env(session, tmp_path, maker):
    st = LocalStorage(str(tmp_path))
    await seed_llm(session, routes=ROUTES, prices=PRICES)
    for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0)):
        await set_setting(session, k, v)
    await session.commit()
    for title, text in (("Hours", HOURS), ("Shipping", SHIP), ("Refunds", "Refunds are accepted within seven days with the receipt."),
                        ("Warranty", "All electronics carry a one year warranty."), ("Payment", "We accept cash on delivery and bKash.")):
        d = Document(title=title, file_path=st.save(f"{title}.txt", text.encode()), type="txt")
        session.add(d)
        await session.commit()
        await process_document(session, d.id, storage=st, embedder=EMB)
    fake = FakeLLM({"small": "Answer from small model", "big": "Answer from big model"})
    return type("Env", (), {"fake": fake, "llm": LLMService(maker, fake), "session": session})()


async def ask(env, text, *, profile=None, conv=None, persist=True, history=None, channel="messenger"):
    return await run_pipeline(env.session, env.llm, text=text, profile=profile, conversation=conv, channel=channel,
                              persist=persist, history=history)


async def add_qa(session, q, a, category=None):
    vec = (await HashingEmbedder(1024).embed([q]))[0]
    session.add(QAPair(question=q, question_norm=normalize_question(q), answer_template=a, category=category, embedding=vec))
    await session.commit()


# ------------------------------------------------------------------------------------------- shortcuts
async def test_exact_qa_shortcut_fills_variables_with_zero_llm_calls(env):
    env.session.add(Variable(key="open_time", value="9 am"))
    await add_qa(env.session, "What are your opening hours?", "We open at {open_time} daily.")
    res = await ask(env, "what are your OPENING hours")
    assert res.path == "qa_shortcut" and res.text == "We open at 9 am daily."
    assert env.fake.calls == [] and res.debug["qa_match"]["exact"] is True


async def test_qa_with_unresolved_variable_is_not_sent_to_the_user(env):
    await add_qa(env.session, "Where are you located?", "Visit us at {address}")
    res = await ask(env, "Where are you located?")
    assert "{address}" not in res.text and res.path != "qa_shortcut"


async def test_qa_with_unresolved_variable_hands_the_rest_of_its_answer_to_the_ai(env):
    env.session.add(Variable(key="open_time", value="9 am"))
    await add_qa(env.session, "Where are you located?", "Visit us at {address}. We open at {open_time} every day.")
    res = await ask(env, "Where are you located?")
    assert res.path.startswith("llm") and res.text == "Answer from small model"
    sent = json.dumps([m for _, req in env.fake.calls for m in req.messages], ensure_ascii=False, default=str)
    assert "We open at 9 am every day." in sent and "Visit us at" not in sent and "{address}" not in sent
    assert any("goes to the AI" in s for s in res.debug["steps"])


async def test_fee_question_without_a_program_is_answered_from_qa(env):
    env.session.add(Variable(key="course_fee", value="১১,৫০০ টাকা"))
    await add_qa(env.session, "কোর্সের ফি কত?", "কোর্সের ফি {course_fee}।")
    res = await ask(env, "কোর্সের ফি কত?")
    assert res.path == "qa_shortcut" and res.text == "কোর্সের ফি ১১,৫০০ টাকা।" and env.fake.calls == []


@pytest.mark.parametrize("text", ["মানি রিসিট লাগবে", "বিকাশে টাকা কেটে গেছে", "আমি যেতে পারব না, টাকা ফেরত পাব?",
                                  "Can I pay in instalments?", "I paid but did not get confirmation", "taka pathaisi"])
async def test_payment_questions_go_to_a_person_and_never_to_the_ai(env, text):
    await add_qa(env.session, text, "Some saved answer")
    res = await ask(env, text)
    assert res.path == "payment_disabled" and env.fake.calls == []


async def test_the_routers_reading_of_the_request_reaches_the_answering_model(env):
    res = await run_pipeline(env.session, env.llm, text="shipping kemon?", profile=None, conversation=None,
                             channel="messenger", persist=False, guest_request="How long delivery takes and what it costs")
    sent = json.dumps([m for _, req in env.fake.calls if _ in ("small", "big") for m in req.messages], ensure_ascii=False)
    assert "(WHAT THE GUEST WANTS: How long delivery takes and what it costs)" in sent and "shipping kemon?" in sent
    assert res.debug["guest_request"] == "How long delivery takes and what it costs"


async def test_a_long_schedule_never_pushes_the_knowledge_out_of_the_prompt(env):
    from datetime import timedelta
    from app.models import Program
    from app.rag.programs import today_bd
    await set_setting(env.session, "chunk_token_budget", 60)
    for i in range(20):
        env.session.add(Program(name=f"Program {i}", start_date=today_bd() + timedelta(days=i + 1), notes="x " * 80))
    env.session.add(Variable(key="long_value", value="y " * 300))
    await env.session.commit()
    await ask(env, "What warranty do electronics carry?")
    sent = json.dumps([m for _, req in env.fake.calls for m in req.messages], ensure_ascii=False)
    assert "one year warranty" in sent and "PROGRAM Program 19" in sent


async def test_the_routers_language_decides_the_reply_language(env):
    for lang, expected in (("banglish", "REPLY IN: Bangla"), ("en", "REPLY IN: English")):
        env.fake.calls.clear()
        await run_pipeline(env.session, env.llm, text="shipping?", profile=None, conversation=None, channel="messenger",
                           persist=False, guest_language=lang)
        assert expected in json.dumps([m for _, req in env.fake.calls for m in req.messages], ensure_ascii=False)


def test_fill_known_drops_only_the_sentences_that_need_a_missing_value():
    from app.rag.templates import fill_known
    assert fill_known("রক্ত লাগলে জানান। বিস্তারিত: {blood_info}", {}) == "রক্ত লাগলে জানান।"
    assert fill_known("Fee {fee}! Call {phone}.\nBye", {"phone": "123"}) == "Call 123. Bye"
    assert fill_known("{about_org}", {}) == ""


async def test_high_similarity_qa_shortcut(env):
    await set_setting(env.session, "qa_threshold", 0.8)
    await add_qa(env.session, "What are your opening hours?", "9 to 5.")
    res = await ask(env, "what are your opening hour")
    assert res.path == "qa_shortcut" and env.fake.calls == [] and res.debug["qa_match"]["similarity"] >= 0.8


async def test_inactive_qa_is_ignored(env):
    await add_qa(env.session, "Do you ship abroad?", "No.")
    (await env.session.execute(select(QAPair))).scalar_one().active = False
    await env.session.commit()
    assert (await ask(env, "Do you ship abroad?")).path != "qa_shortcut"


# ------------------------------------------------------------------------------------------- retrieval + LLM
async def test_rag_answer_uses_top_k_chunks_small_model_and_returns_debug(env):
    await set_setting(env.session, "top_k", 2)
    await set_setting(env.session, "keyword_search_enabled", False)   # this test is about the hybrid (vector + keyword) fallback
    res = await ask(env, "when is the shop open, what are the hours of the shop")
    assert res.path == "llm" and res.text == "Answer from small model" and env.fake.models() == ["small"]
    assert len(res.debug["chunks"]) == 2 and res.debug["chunks"][0]["document"] == "Hours"
    assert {"similarity", "page", "document_id"} <= set(res.debug["chunks"][0])
    prompt = json.dumps(res.debug["prompt"])
    assert HOURS in prompt and prompt.count("\\n---\\n") == 1               # exactly the 2 retrieved chunks
    assert "Warranty" not in prompt and "electronics" not in prompt          # no whole-database dumps
    assert res.debug["tokens_in"] == 500 and res.debug["cost_usd"] > 0 and res.debug["model"] == "small"
    assert res.tokens_in == 500 and res.tokens_out == 50


async def test_low_confidence_or_complex_questions_escalate_to_the_large_model(env):
    await set_setting(env.session, "escalate_below", 1.0)
    res = await ask(env, "what are the shop hours")
    assert res.path == "llm_large" and env.fake.models() == ["big"] and res.text == "Answer from big model"
    await set_setting(env.session, "escalate_below", 0.0)
    res = await ask(env, "Compare delivery inside and outside Dhaka? Which is cheaper?")
    assert res.path == "llm_large"


async def test_large_route_missing_falls_back_to_standard_route(env):
    from app.models import LLMRoute
    await env.session.delete((await env.session.execute(select(LLMRoute).where(LLMRoute.task == "answer_large"))).scalar_one())
    await set_setting(env.session, "escalate_below", 1.0)
    res = await ask(env, "shop hours")
    assert res.text == "Answer from small model"


async def test_nothing_relevant_gives_canned_reply_without_calling_the_llm_and_logs_unanswered(env):
    await set_setting(env.session, "retrieval_min_similarity", 0.99)
    res = await ask(env, "xyz qwerty zzz", conv=None)
    assert res.path == "no_context" and env.fake.calls == [] and "don't have" in res.text
    row = (await env.session.execute(select(UnansweredQuestion))).scalar_one()
    assert row.question == "xyz qwerty zzz"


async def test_general_answer_fallback_uses_the_model_only_when_enabled(env):
    await set_setting(env.session, "retrieval_min_similarity", 0.99)
    await set_setting(env.session, "general_answer_enabled", True)
    res = await ask(env, "what is the capital of Japan?", persist=False)
    assert res.path == "llm" and res.text == "Answer from small model"
    assert "GENERAL ANSWER MODE" in json.dumps(res.debug["prompt"])


async def test_llm_unavailable_answers_from_the_matching_text_or_degrades_to_canned_reply(env, session):
    from app.models import LLMProviderConfig
    (await session.execute(select(LLMProviderConfig))).scalar_one().enabled = False
    await session.commit()
    res = await ask(env, "when is the shop open on friday")       # words found in a document: quote it, short
    assert res.path == "kb_extract" and "Friday" in res.text and env.fake.calls == []
    res = await ask(env, "xyzzy plugh")                            # only a weak vector match: never quoted
    assert res.path in ("llm_unavailable", "no_context") and "তথ্য নেই" in res.text  # two words: Bangla, the default


async def test_persist_false_leaves_no_trace(env):
    await set_setting(env.session, "retrieval_min_similarity", 0.99)
    await ask(env, "nothing matches this", persist=False)
    await ask(env, "what are the shop hours", persist=False)
    await set_setting(env.session, "retrieval_min_similarity", 0.05)
    await ask(env, "what are the shop hours", persist=False)
    await env.session.commit()
    n = lambda t: env.session.scalar(select(func.count()).select_from(t))  # noqa: E731
    assert await n(UnansweredQuestion) == 0 and await n(SemanticCache) == 0
    assert await n(LLMCall) >= 1   # money was still spent, so it is still logged


# ------------------------------------------------------------------------------------------- cache
async def test_semantic_cache_serves_repeat_questions_without_the_llm(env):
    first = await ask(env, "what are the shop hours")
    assert first.path == "llm" and first.debug["cached"] is True
    second = await ask(env, "what are the shop hours?")
    assert second.path == "cache" and second.text == first.text and len(env.fake.calls) == 1
    assert second.debug["cache"]["similarity"] >= 0.95


async def test_cache_is_invalidated_when_the_knowledge_changes(env):
    await ask(env, "what are the shop hours")
    from app.rag.documents import bump_context_version
    await bump_context_version(env.session)
    await env.session.commit()
    assert (await ask(env, "what are the shop hours")).path == "llm" and len(env.fake.calls) == 2


async def test_questions_containing_pii_are_never_cached(env):
    res = await ask(env, "what are the shop hours? call me on 01712345678")
    assert res.path == "llm" and not res.debug.get("cached")
    assert (await env.session.scalar(select(func.count()).select_from(SemanticCache))) == 0
    assert "01712345678" not in json.dumps(res.debug["prompt"])


async def test_answers_that_use_placeholders_are_never_cached(env):
    env.fake.script["small"] = lambda req: "We will call you on [PHONE_1]."
    res = await ask(env, "shop hours? my phone is 01712345678, call me")
    assert "01712345678" in res.text                                    # restored for this user
    assert (await env.session.scalar(select(func.count()).select_from(SemanticCache))) == 0


# ------------------------------------------------------------------------------------------- output filter
async def test_foreign_contact_in_the_answer_is_blocked(env):
    env.fake.script["small"] = "Sure! Karim's number is 01999888777."
    res = await ask(env, "what are the shop hours")
    assert res.debug["output_blocked"] and "01999888777" not in res.text and "foreign_contact_data" in res.debug["output_reasons"]
    assert res.path == "kb_extract" and "9" in res.text                          # the hours text itself answers instead
    assert (await env.session.scalar(select(func.count()).select_from(SemanticCache))) == 0
    assert (await env.session.execute(select(UnansweredQuestion.reason))).scalar_one() == "output_blocked"


async def test_business_variable_values_may_be_stated(env):
    env.session.add(Variable(key="shop_phone", value="01811223344"))
    await env.session.commit()
    env.fake.script["small"] = "Call the shop on 01811223344."
    assert not (await ask(env, "what are the shop hours")).debug["output_blocked"]


# ------------------------------------------------------------------------------------------- profile in prompt
async def test_profile_summary_and_missing_fields_only_after_consent(env):
    r = await resolve(env.session, mk_msg("messenger", "P1", "hi", "1"))
    p = r.profile
    await C.record_consent(env.session, p, True)
    await C.apply_field(env.session, p, "name", "Rahim Uddin")
    await C.apply_field(env.session, p, "profession", "Teacher")
    await env.session.commit()
    res = await ask(env, "what are the shop hours", profile=p, conv=r.conversation)
    last = res.debug["prompt"]["messages"][-1]["content"]
    assert "PROFILE: name: [NAME_1]; profession: Teacher" in last and "MISSING" not in last  # asked only when a request needs it
    assert "Rahim" not in json.dumps(res.debug["prompt"])
    assert res.debug["profile_injected"]["missing_fields"] == []
    # without consent the model is not even told what to collect
    r2 = await resolve(env.session, mk_msg("messenger", "P2", "hi", "2"))
    res2 = await ask(env, "what are the shop hours", profile=r2.profile, conv=r2.conversation)
    assert "MISSING" not in res2.debug["prompt"]["messages"][-1]["content"]


async def test_history_is_limited_and_current_message_excluded(env):
    r = await resolve(env.session, mk_msg("messenger", "H1", "hi", "1"))
    for i in range(8):
        env.session.add(Message(conversation_id=r.conversation.id, role="user" if i % 2 == 0 else "assistant", content_redacted=f"turn {i}"))
    env.session.add(Message(conversation_id=r.conversation.id, role="user", content_redacted="what are the shop hours"))
    await env.session.commit()
    res = await ask(env, "what are the shop hours", profile=r.profile, conv=r.conversation)
    sent = json.dumps(res.debug["prompt"])
    assert "turn 7" in sent and "turn 4" in sent and "turn 3" not in sent
    assert sent.count("what are the shop hours") == 1


async def test_short_follow_up_is_rewritten_before_qa_retrieval_and_original_reaches_answer_prompt(env):
    env.session.add(LLMRoute(task="rewrite", primary_provider="anthropic", primary_model="rewrite",
                             fallback_provider=None, fallback_model=None, max_tokens=80, temperature=0))
    await add_qa(env.session, "What are your opening hours?", "We are open 9 to 5.")
    await env.session.commit()
    env.fake.script["rewrite"] = "What are your opening hours?"
    res = await ask(env, "Oita koto?", history=[("user", "Tell me about your opening hours"), ("assistant", "Sure")])
    assert res.path == "qa_shortcut" and res.text == "We are open 9 to 5."
    assert res.debug["question_rewrite"] == {"original": "Oita koto?", "rewritten": "What are your opening hours?", "used": True}
    assert env.fake.models() == ["rewrite"]
    assert (await env.session.execute(select(LLMCall.task))).scalar_one() == "rewrite"


async def test_missing_rewrite_route_keeps_old_short_follow_up_concatenation(env):
    res = await ask(env, "oita", history=[("user", "What are the shop hours?")])
    assert res.debug["question_rewrite"]["used"] is False
    assert res.debug["question_rewrite"]["rewritten"] == "What are the shop hours? oita"
    assert "Rewrite unavailable" in res.debug["steps"][0]


# ------------------------------------------------------------------------------------------- extraction / summaries
async def test_extraction_saves_fields_after_consent_and_rejects_sensitive(env):
    await set_setting(env.session, "info_collection_enabled", False)  # the every-N-messages mode
    r = await resolve(env.session, mk_msg("messenger", "E1", "hello", "1"))
    p, conv = r.profile, r.conversation
    await C.record_consent(env.session, p, True)
    for i in range(4):
        env.session.add(Message(conversation_id=conv.id, role="user", content_redacted=f"msg {i}"))
    await env.session.commit()
    env.fake.script["small"] = ('```json\n{"name":"Rahim","age":"34","profession":"Teacher","location":null,'
                                '"facts":{"hobby":"chess","password":"hunter2","card_note":"4111 1111 1111 1111"}}\n```')
    out = await run_post_reply(env.session, env.llm, p, conv, "messenger")
    st = {e["key"]: e["status"] for e in out["extracted"]}
    assert st["name"] == st["age"] == st["profession"] == st["hobby"] == "saved"
    assert st["password"] == "rejected" and st["card_note"] == "rejected"
    assert (p.name, p.age) == ("Rahim", 34)
    assert p.summary and "Rahim" in p.summary and not p.summary_dirty            # dashboard copy, no contacts
    sent = json.dumps(env.fake.calls[-1][1].audit_payload)
    assert "msg 3" in sent and "01712" not in sent


async def test_extraction_without_consent_holds_values_pending(env):
    await set_setting(env.session, "info_collection_enabled", False)  # the every-N-messages mode
    r = await resolve(env.session, mk_msg("messenger", "E2", "hello", "1"))
    for i in range(4):
        env.session.add(Message(conversation_id=r.conversation.id, role="user", content_redacted=f"m{i}"))
    await env.session.commit()
    env.fake.script["small"] = '{"name": "Karim"}'
    out = await run_post_reply(env.session, env.llm, r.profile, r.conversation, "messenger")
    assert out["extracted"] == [{"key": "name", "status": "pending"}] and r.profile.name is None


async def test_extraction_runs_only_every_n_messages_and_tolerates_garbage(env):
    await set_setting(env.session, "info_collection_enabled", False)  # the every-N-messages mode
    r = await resolve(env.session, mk_msg("messenger", "E3", "hello", "1"))
    env.session.add(Message(conversation_id=r.conversation.id, role="user", content_redacted="one"))
    await env.session.commit()
    assert "extracted" not in await run_post_reply(env.session, env.llm, r.profile, r.conversation, "messenger")
    assert env.fake.calls == []                                           # 1 message: no extraction call
    for i in range(3):
        env.session.add(Message(conversation_id=r.conversation.id, role="user", content_redacted=f"x{i}"))
    await env.session.commit()
    env.fake.script["small"] = "I am sorry, I cannot do that"
    assert (await run_post_reply(env.session, env.llm, r.profile, r.conversation, "messenger"))["extracted"] == []


async def test_rolling_conversation_summary_replaces_full_history(env):
    await set_setting(env.session, "summarize_every_n_messages", 4)
    await set_setting(env.session, "extraction_every_n_messages", 0)
    r = await resolve(env.session, mk_msg("messenger", "S1", "hi", "1"))
    for i in range(8):
        env.session.add(Message(conversation_id=r.conversation.id, role="user" if i % 2 == 0 else "assistant", content_redacted=f"line {i}"))
    await env.session.commit()
    env.fake.script["small"] = "User asked about shipping; wants Dhaka delivery."
    out = await run_post_reply(env.session, env.llm, r.profile, r.conversation, "messenger")
    assert out["summarized"] and r.conversation.summary.startswith("User asked about shipping")
    sent = json.dumps(env.fake.calls[-1][1].audit_payload)
    assert "line 0" in sent and "line 3" in sent and "line 7" not in sent   # the last N stay verbatim, older are summarized
    res = await ask(env, "what are the shop hours", profile=r.profile, conv=r.conversation)
    assert "EARLIER: User asked about shipping" in res.debug["prompt"]["messages"][-1]["content"]


async def test_test_chat_questions_do_not_pollute_the_unanswered_list(env):
    await set_setting(env.session, "retrieval_min_similarity", 0.99)
    await run_pipeline(env.session, env.llm, text="asked from the test chat", profile=None, conversation=None, channel="messenger",
                       persist=True, is_test=True)
    await env.session.commit()
    assert await env.session.scalar(select(func.count()).select_from(UnansweredQuestion)) == 0

async def test_qa_wording_lookup_stays_fast_with_5000_pairs(env):
    """The wording shortcut may load its versioned index once, but must not scan every row per message."""
    embed = (await HashingEmbedder(1024).embed(["seed"]))[0]
    env.session.add_all([
        QAPair(question=f"Program registration information {n}", question_norm=normalize_question(f"Program registration information {n}"),
               answer_template=f"Answer {n}", embedding=embed)
        for n in range(5000)
    ])
    await env.session.commit()
    # Warm the one-time, context-versioned metadata index outside the measured request.
    from app.rag.qa_index import active_qa_index
    await active_qa_index(env.session)
    started = time.perf_counter()
    result = await ask(env, "Program registration information 4999")
    assert result.path == "qa_shortcut" and result.text == "Answer 4999"
    assert time.perf_counter() - started < 1.0
