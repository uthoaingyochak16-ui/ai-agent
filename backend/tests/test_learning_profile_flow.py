"""Guest profile request flow, the organised knowledge (categories, variables, contacts, keywords) and the safe
learning queue. One test per requirement (a) - (n)."""
import json

import pytest
from sqlalchemy import func, select

from app.llm.providers import LLMResult, ToolCall
from app.llm.service import LLMService
from app.models import (Conversation, Document, KeywordEntry, Message, Profile, QAPair, ToolCallLog,
                        UnansweredQuestion, Variable)
from app.rag.categories import ALIASES, CATEGORIES, migrate_category, standard_category
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder, embed_texts, embedding_signature
from app.rag.keyword_index import rebuild_index
from app.rag.keyword_search import keyword_retrieve
from app.rag.templates import normalize_question
from app.services import consent as C
from app.services import lead as lead_svc
from app.services import learning
from app.services import tools as tools_svc
from app.services.ingest import handle_message
from app.services.pipeline import run_pipeline
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api2 import admin, agent  # noqa: F401 - fixtures

EMB = (HashingEmbedder(1024), None)
COURSE = "The meditation course runs for four days. Registration for the meditation course is open at every branch."
ASK_WORDS = DEFAULTS["lead_prompt"][:25]
CONTACTS = [
    {"question": "Quantum Foundation central office (head office) address and phone — কেন্দ্রীয় দফতর ঠিকানা",
     "answer": "Office: Central office (Head office), Shantinagar, Dhaka-1217. Phone: +88 01714 974333.",
     "keywords": "head office, central office, কেন্দ্রীয় দফতর, হেড অফিস, ঠিকানা, address, thikana"},
    {"question": "Mirpur Centre address — মিরপুর সেন্টার ঠিকানা",
     "answer": "Office: Mirpur Centre. Address: House 5/2, Block E, Road 6, Mirpur-2, Dhaka-1216. Mobile: +88 01870 725921.",
     "keywords": "Mirpur, মিরপুর, centre, সেন্টার, ঠিকানা, address, thikana"},
    {"question": "স্বেচ্ছা রক্তদান কার্যক্রম ও কোয়ান্টাম ল্যাব — Blood donation and Quantum Lab",
     "answer": "রক্তদান কার্যক্রম ও কোয়ান্টাম ল্যাব: শান্তিনগর, ঢাকা-১২১৭।",
     "keywords": "রক্তদান, রক্ত, blood donation, blood, rokto dan, rokto, quantum lab, ল্যাব"},
]


async def _doc(session, tmp_path, records, title):
    st = LocalStorage(str(tmp_path))
    d = Document(title=title, file_path=st.save(f"{title[:8]}.json", json.dumps(records, ensure_ascii=False).encode()), type="json")
    session.add(d)
    await session.commit()
    assert await process_document(session, d.id, storage=st, embedder=EMB) == "ready"
    return d


@pytest.fixture
async def guest(session, maker, tmp_path):
    """Lead capture on, one course document, the answer model and an extraction model."""
    async def make(extract: dict | None = None):
        await seed_llm(session, routes={"answer": ("anthropic", "small", None, None),
                                        "extraction": ("anthropic", "ext", None, None)})
        for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0),
                     ("lead_capture_enabled", True)):
            await set_setting(session, k, v)
        st = LocalStorage(str(tmp_path))
        d = Document(title="Course", file_path=st.save("c.txt", COURSE.encode()), type="txt")
        session.add(d)
        await session.commit()
        await process_document(session, d.id, storage=st, embedder=EMB)
        fake = FakeLLM({"small": "The meditation course runs for four days.",
                        "ext": lambda req: json.dumps(extract or {"name": None, "location": None})})
        return fake, LLMService(maker, fake)
    return make


async def send(session, llm, text, mid, uid="test:guest"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def bot(session):
    return [m.content_redacted for m in (await session.execute(
        select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def new_conversation(session):
    """Close the open conversation: the guest's next message starts a new one (same profile)."""
    for c in (await session.execute(select(Conversation).where(Conversation.status != "closed"))).scalars():
        c.status = "closed"
    await session.commit()


async def the_profile(session) -> Profile:
    p = (await session.execute(select(Profile))).scalar_one()
    await session.refresh(p)
    return p


async def asked(session, llm, text, mid):
    """The guest's message, then the details are asked for in the flow (as create_lead does when they want to join)."""
    r = await send(session, llm, text, mid)
    lead_svc.mark_asked(await the_profile(session), await session.get(Conversation, r.conversation.id))
    await session.commit()
    return r


# ---- A. guest profile / personal data ---------------------------------------------------------------------------
async def test_a_new_guest_is_never_asked_for_details_unprompted(session, guest):
    _, llm = await guest()
    for i, q in enumerate(("meditation course koto din?", "registration kivabe?", "course fee koto?", "ar ki ache?")):
        await send(session, llm, q, f"a{i}")
    replies = await bot(session)
    assert all("four days" in t for t in replies) and not any(ASK_WORDS in t or "ফোন নম্বর" in t for t in replies)
    assert (await the_profile(session)).personal_info_request_status == "not_asked"


async def test_b_details_given_are_never_asked_again_in_a_later_conversation(session, guest):
    _, llm = await guest({"name": "Rahim", "location": None, "reply": "details"})
    await asked(session, llm, "meditation course koto din?", "b1")
    await send(session, llm, "Rahim, 01712345678", "b2")
    p = await the_profile(session)
    assert (p.name, C.get_phone(p), p.personal_info_request_status) == ("Rahim", "+8801712345678", "provided")
    await new_conversation(session)
    await send(session, llm, "registration kivabe korbo?", "b3")
    assert (await session.execute(select(func.count()).select_from(Conversation))).scalar_one() == 2
    assert ASK_WORDS not in (await bot(session))[-1] and "four days" in (await bot(session))[-1]


async def test_c_declined_guest_is_not_asked_in_a_later_conversation(session, guest):
    _, llm = await guest({"name": None, "location": None, "reply": "refused"})
    await asked(session, llm, "meditation course koto din?", "c1")
    await send(session, llm, "na, dibo na", "c2")
    assert (await the_profile(session)).personal_info_request_status == "declined"
    await new_conversation(session)
    for i, q in enumerate(("course fee koto?", "registration kothay?")):
        await send(session, llm, q, f"c3{i}")
        assert ASK_WORDS not in (await bot(session))[-1]


class ToolFake:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, name, key, base, timeout, retries):
        outer = self

        class P:
            async def generate(self, req, *, model, max_tokens, temperature, tools=None):
                outer.calls.append((req, tools))
                text, calls = outer.script.pop(0) if outer.script else ("ok", [])
                return LLMResult(text=text, tokens_in=10, tokens_out=5, provider=name, model=model, tool_calls=list(calls))
        return P()


async def test_d_callback_asks_for_a_phone_only_when_the_profile_has_none(session, maker, tmp_path):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None), "answer_large": ("anthropic", "small", None, None)})
    for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0),
                 ("tools_enabled", {"request_callback": True})):
        await set_setting(session, k, v)
    st = LocalStorage(str(tmp_path))
    d = Document(title="Course", file_path=st.save("c.txt", COURSE.encode()), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=EMB)
    call = ToolCall("c1", "request_callback", {"reason": "meditation course"})

    # no phone on the profile: the tool tells the model to ask, once
    fake = ToolFake([("", [call]), ("Apnar phone number ta dile amra call korbo.", [])])
    await send(session, LLMService(maker, fake), "meditation course niye amake call korben?", "d1", uid="test:nophone")
    results = [m["content"] for m in fake.calls[1][0].messages if m["role"] == "tool"]
    assert results and results[0].startswith("No phone number is on file")
    assert [r.result for r in (await session.execute(select(ToolCallLog))).scalars()] == [tools_svc.NO_PHONE]

    # phone on the profile: straight to the confirmation question, nothing asked
    p = await the_profile(session)
    C.set_phone(p, "+8801712345678")
    await session.commit()
    fake2 = ToolFake([("", [call])])
    await send(session, LLMService(maker, fake2), "meditation course niye amake call korben?", "d2", uid="test:nophone")
    assert (await bot(session))[-1].startswith("আমি এটি করতে চাই")
    assert "Already on file for this person: phone number" in fake2.calls[0][0].system
    assert (await session.execute(select(func.count()).select_from(ToolCallLog).where(
        ToolCallLog.result == tools_svc.NO_PHONE))).scalar_one() == 1


# ---- C. reply behaviour ------------------------------------------------------------------------------------------
async def test_e_general_question_gets_an_ai_answer(session, maker):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "general_answer_enabled", True)
    await session.commit()
    fake = FakeLLM({"small": "Paris is the capital of France."})
    res = await run_pipeline(session, LLMService(maker, fake), text="What is the capital of France?", profile=None,
                             conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path == "llm" and res.text == "Paris is the capital of France."


async def test_f_organisation_question_without_knowledge_is_not_guessed(session, maker):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "general_answer_enabled", True)
    await session.commit()
    fake = FakeLLM({"small": "Our Sylhet office is at 12 Fake Road."})
    llm = LLMService(maker, fake)
    for q, path in (("Quantum er Sylhet office er address ki?", "no_context"), ("আপনাদের রংপুর শাখার নম্বর কত?", "no_context"),
                    ("What is your refund policy?", "payment_disabled")):        # refunds always go to a person
        res = await run_pipeline(session, llm, text=q, profile=None, conversation=None, channel="messenger", embedder=EMB)
        assert res.path == path and res.text in (DEFAULTS["no_answer_reply"], DEFAULTS["no_answer_reply_en"])
    assert fake.models() == []                                                   # the model was never asked
    assert (await session.execute(select(func.count()).select_from(UnansweredQuestion))).scalar_one() == 3


async def test_g_contact_question_is_answered_from_the_contact_qa_or_chunk(session, maker, tmp_path):
    await _doc(session, tmp_path, CONTACTS, "Quantum Foundation — Offices, Addresses & Contacts")
    q = "Quantum Foundation head office kothay?"
    vec = (await embed_texts(session, [q], embedder=EMB))[0]
    session.add(QAPair(question=q, question_norm=normalize_question(q), category="Contact & Address",
                       answer_template="কেন্দ্রীয় দফতর: শান্তিনগর, ঢাকা-১২১৭।", embedding=vec,
                       embedding_version=await embedding_signature(session, embedder=EMB)))
    await session.commit()
    llm = LLMService(maker, FakeLLM())
    res = await run_pipeline(session, llm, text=q, profile=None, conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path == "qa_shortcut" and "শান্তিনগর" in res.text and res.debug["qa_match"]["category"] == "Contact & Address"

    kw = await keyword_retrieve(session, llm, text="Mirpur office address", retrieval_text="Mirpur office address",
                                history=[], channel=None, conversation_id=None, is_test=True, allow_ai=False)
    assert kw.usable and "Mirpur-2" in (kw.hits[0].text or kw.hits[0].chunk.content)


async def test_h_category_filter_and_standard_categories(client, admin):  # noqa: F811
    for q, cat in (("Mirpur office address?", "contact"), ("Course fee koto?", "fee"), ("Rokto lagbe", None)):
        assert (await client.post("/api/qa", headers=admin, json={"question": q, "answer_template": "x", "category": cat})).status_code == 200
    lst = (await client.get("/api/qa", headers=admin, params={"category": "Contact & Address"})).json()
    assert [i["question"] for i in lst["items"]] == ["Mirpur office address?"]     # "contact" was stored as the standard name
    assert lst["categories"][:len(CATEGORIES)] == list(CATEGORIES)
    fee = (await client.get("/api/qa", headers=admin, params={"category": "Fees & Payment"})).json()
    assert fee["total"] == 1


async def test_i_variables_fill_answers_and_an_empty_one_never_shows_blank(session, maker):
    q1, q2 = "Head office er phone number koto?", "Office kokhon khola thake?"
    sig = await embedding_signature(session, embedder=EMB)
    for q, tpl in ((q1, "কেন্দ্রীয় দফতরের নম্বর: {head_office_phone}"), (q2, "অফিস সময়: {office_hours}")):
        session.add(QAPair(question=q, question_norm=normalize_question(q), answer_template=tpl, category="Contact & Address",
                           embedding=(await embed_texts(session, [q], embedder=EMB))[0], embedding_version=sig))
    session.add_all([Variable(key="head_office_phone", value="+88 01714 974333"), Variable(key="office_hours", value="")])
    await session.commit()
    llm = LLMService(maker, FakeLLM())
    res = await run_pipeline(session, llm, text=q1, profile=None, conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path == "qa_shortcut" and res.text == "কেন্দ্রীয় দফতরের নম্বর: +88 01714 974333"
    res = await run_pipeline(session, llm, text=q2, profile=None, conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path != "qa_shortcut" and "অফিস সময়: ।" not in res.text      # not filled in yet: not used


# ---- B4 / D. the Unknown-questions queue --------------------------------------------------------------------------
async def test_j_a_repeated_unknown_question_raises_its_count(session):
    for q in ("Quantum er Sylhet office kothay?", "quantum er sylhet office kothay", "Quantum er Sylhet office kothay??"):
        await learning.record_unknown(session, q, channel="messenger")
    await learning.record_unknown(session, "Blood bank e ki weekend e khola?", channel="messenger")
    await session.commit()
    rows = {r.question_norm: r for r in (await session.execute(select(UnansweredQuestion))).scalars()}
    assert len(rows) == 2
    sylhet = rows["quantum er sylhet office kothay"]
    assert sylhet.occurrences == 3 and sylhet.status == "new" and sylhet.suggested_category == "Contact & Address"
    assert "sylhet" in sylhet.suggested_keywords
    assert rows[normalize_question("Blood bank e ki weekend e khola?")].suggested_category == "Blood Donation"


async def test_k_bangla_english_and_banglish_find_the_same_record(session, maker, tmp_path):
    await _doc(session, tmp_path, CONTACTS, "Quantum Foundation — Offices, Addresses & Contacts")
    llm = LLMService(maker, FakeLLM())
    found = set()
    for q in ("রক্তদান কোথায় হয়?", "blood donation kothay?", "rokto dan kothay hoy?"):
        kw = await keyword_retrieve(session, llm, text=q, retrieval_text=q, history=[], channel=None, conversation_id=None,
                                    is_test=True, allow_ai=False)
        assert kw.usable, q
        found.add(kw.hits[0].chunk.id)
    assert len(found) == 1


async def test_l_guest_data_never_reaches_the_knowledge_or_keyword_index(session, guest):
    _, llm = await guest({"name": "Shahnaz Parvin", "location": "Gopalganj", "reply": "details"})
    await send(session, llm, "Quantum er Barguna branch e ki course hoy?", "l1")
    await send(session, llm, "Shahnaz Parvin, 01712345699, Gopalganj", "l2")
    await send(session, llm, "amar number 01712345699, Barguna office er address ki?", "l3")
    await learning.refresh_suggestions(session)
    await rebuild_index(session)
    await session.commit()
    terms = {t.lower() for t in (await session.execute(select(KeywordEntry.term))).scalars()}
    assert not {"shahnaz", "parvin", "gopalganj"} & terms and not any("1712345699" in t for t in terms)
    for model in (QAPair, Variable):
        assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0
    stored = " ".join(f"{u.question} {u.suggested_keywords}" for u in (await session.execute(select(UnansweredQuestion))).scalars())
    assert "01712345699" not in stored and "Shahnaz" not in stored


async def test_m_a_draft_is_never_live_until_the_admin_saves_it(session, client, admin, maker):  # noqa: F811
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    q = "Quantum er Barishal branch kothay?"
    row = await learning.record_unknown(session, q)
    row.draft_answer = "বরিশাল শাখা: জর্ডন রোড, বরিশাল।"
    await session.commit()
    res = await run_pipeline(session, LLMService(maker, FakeLLM()), text=q, profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert res.path == "no_context" and "জর্ডন" not in res.text                 # the draft is not an answer
    assert (await session.execute(select(func.count()).select_from(QAPair))).scalar_one() == 0
    unknown = (await client.get("/api/unanswered", headers=admin)).json()
    assert unknown[0]["draft_answer"] == row.draft_answer and unknown[0]["suggested_category"] == "Contact & Address"

    r = await client.post("/api/qa", headers=admin, json={"question": q, "answer_template": unknown[0]["draft_answer"],
                                                          "category": unknown[0]["suggested_category"],
                                                          "resolve_unanswered_id": row.id})
    assert r.status_code == 200
    await session.refresh(row)
    assert row.status == "approved" and row.resolved
    assert (await client.get("/api/unanswered", headers=admin)).json() == []


async def test_m_wrong_answer_feedback_queues_a_review_item(session, client, admin):  # noqa: F811
    r = await client.post("/api/cache/feedback", headers=admin,
                          json={"answer": "Course fee 500 taka.", "question": "Course fee koto?"})
    assert r.status_code == 200
    row = (await session.execute(select(UnansweredQuestion))).scalar_one()
    assert (row.reason, row.wrong_answer, row.status) == ("wrong_answer", "Course fee 500 taka.", "new")
    assert (await session.execute(select(func.count()).select_from(QAPair))).scalar_one() == 0


# ---- F. migration -------------------------------------------------------------------------------------------------
LIVE_CATEGORIES = ["course", "support", "general", "org", "fee", "service", "eligibility", "misc", "events", "method",
                   "complaint", "registration", "partner", "books", "privacy", "tech", "contact", "location", "about"]


def test_n_every_existing_category_maps_to_a_standard_one_and_is_restorable():
    for old in LIVE_CATEGORIES:
        assert old in ALIASES and standard_category(old) in CATEGORIES
    assert standard_category(None) is None and standard_category("Contact & Address") == "Contact & Address"
    assert migrate_category("contact", "আপনাদের অফিস কখন খোলা থাকে?", "") == "Contact & Address"
    assert migrate_category("service", "রক্ত লাগবে, কোথায় যোগাযোগ করব?", "") == "Blood Donation"
    assert migrate_category("misc", "Quantum lab er report kobe pabo?", "") == "Quantum Lab"
    assert migrate_category("fee", "কোর্স ফি কত?", "") == "Fees & Payment"


async def test_n_existing_qa_documents_and_variables_still_answer(session, maker, tmp_path):
    """Rows stored the old way (old category name, variables, a document) keep working with the new code."""
    q = "Course fee koto?"
    session.add(QAPair(question=q, question_norm=normalize_question(q), answer_template="ফি {course_fee} টাকা", category="fee",
                       embedding=(await embed_texts(session, [q], embedder=EMB))[0],
                       embedding_version=await embedding_signature(session, embedder=EMB)))
    session.add(Variable(key="course_fee", value="11500"))
    await session.commit()
    await _doc(session, tmp_path, CONTACTS, "kb")
    llm = LLMService(maker, FakeLLM())
    res = await run_pipeline(session, llm, text=q, profile=None, conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.text == "ফি 11500 টাকা"
    kw = await keyword_retrieve(session, llm, text="Mirpur centre address", retrieval_text="Mirpur centre address", history=[],
                                channel=None, conversation_id=None, is_test=True, allow_ai=False)
    assert kw.usable
