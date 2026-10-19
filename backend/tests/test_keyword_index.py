import json
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

from app.llm.service import LLMService
from app.models import Chunk, Document, KeywordEntry, Program, QAPair, Variable
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.rag.keyword_index import build_rows, ensure_index, rebuild_index, stats, stem_of
from app.rag.keyword_search import KeywordPlan, keyword_retrieve, parse_plan, search_index
from app.rag.programs import today_bd
from app.services.pipeline import run_pipeline
from app.services.settings_service import set_setting
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api2 import admin, agent  # noqa: F401 - fixtures

EMB = (HashingEmbedder(1024), None)
RECORDS = [
    {"id": "r1", "topic": "প্রতিষ্ঠান পরিচিতি", "keywords": "['সেবা', 'রক্তদান', 'দাফন']", "question": "কোয়ান্টাম ফাউন্ডেশন কী কী সেবা দেয়?",
     "short_answer": "রক্তদান, দাফন, ত্রাণ ও শিক্ষাবৃত্তি সেবা দেয়।"},
    {"id": "r2", "topic": "অভিভাবকত্ব", "keywords": "['সন্তান', 'অস্থিরতা']",
     "question": "আমার ছেলেকে কোয়ান্টাম মেথড কোর্স করিয়েছি, সে অস্থির। কী করব?", "short_answer": "আগে আপনাকে প্রশান্ত হতে হবে।"},
    {"id": "r3", "topic": "প্রেম", "keywords": "['প্রেম', 'আকর্ষণ', 'ভালবাসা']",
     "question": "কাউকে ভালো লাগলে নিজেকে কীভাবে সামলাব?", "short_answer": "এটাকে সুন্দর স্মৃতি হিসেবে রেখে দিন।"},
]


def test_stems_join_inflections_and_scripts_stay_apart():
    assert stem_of("প্রেমের") == stem_of("প্রেমে") == stem_of("প্রেম")
    assert stem_of("কোর্সের") == stem_of("কোর্সে") == stem_of("কোর্স")
    assert stem_of("courses") == stem_of("course") and stem_of("fees") == stem_of("fee") and stem_of("class") == "class"
    assert stem_of("ফি") == "ফি"       # too short to strip


def test_rows_are_weighted_by_where_the_word_appears():
    rows = {r["term"]: r for r in build_rows("chunk", "1", 7, title="কোর্স ফি", meta="['রেজিস্ট্রেশন']", doc="heart club",
                                            body="ভর্তি ভর্তি ফি অফিস")}
    assert rows["কোর্স"]["weight"] == 3.0 and rows["রেজিস্ট্রেশন"]["weight"] == 2.5 and rows["heart"]["weight"] == 1.5
    assert rows["ভর্তি"]["weight"] == 1.0 and rows["ফি"]["weight"] == 3.0       # the strongest place wins
    assert all(r["document_id"] == 7 and r["source_type"] == "chunk" for r in rows.values()) and len(rows) <= 48


async def _doc(session, tmp_path, records=RECORDS, title="kb"):
    st = LocalStorage(str(tmp_path))
    d = Document(title=title, file_path=st.save("kb.json", json.dumps(records, ensure_ascii=False).encode()), type="json")
    session.add(d)
    await session.commit()
    assert await process_document(session, d.id, storage=st, embedder=EMB) == "ready"
    return d


async def terms_of_source(session, st, ref):
    return {e.term for e in (await session.execute(select(KeywordEntry).where(
        KeywordEntry.source_type == st, KeywordEntry.source_ref == str(ref)))).scalars()}


async def test_keywords_written_in_the_file_are_indexed_and_the_index_follows_the_data(session, tmp_path):
    d = await _doc(session, tmp_path)
    c = (await session.execute(select(Chunk).where(Chunk.question.like("%সেবা দেয়%")))).scalars().first()
    assert "রক্তদান" in c.keywords and "সেবা" in c.keywords                # the file's own keywords travel with the chunk
    assert {"সেবা", "রক্তদান", "দাফন", "ফাউন্ডেশন"} <= await terms_of_source(session, "chunk", c.id)

    c.keywords = "['হাসপাতাল']"                                              # editing re-indexes it
    await session.commit()
    rows = {e.term: e.weight for e in (await session.execute(select(KeywordEntry).where(
        KeywordEntry.source_type == "chunk", KeywordEntry.source_ref == str(c.id)))).scalars()}
    assert rows["হাসপাতাল"] == 2.5 and rows["দাফন"] == 1.0    # still in the text (body, 1.0) but no longer a file keyword (2.5)

    session.add_all([QAPair(question="কোর্স ফি কত?", question_norm="কোর্স ফি কত", answer_template="{Course_fee}", category="fee",
                            embedding=(await EMB[0].embed(["x"]))[0]),
                     Variable(key="Office_phone", value="017", description="অফিসের ফোন নম্বর"),
                     Program(name="Yoga Orientation", start_date=today_bd() + timedelta(days=5), place="কাকরাইল")])
    await session.commit()
    assert {"কোর্স", "ফি", "fee"} <= await terms_of_source(session, "qa", 1)
    assert {"office", "phone", "অফিসের", "ফোন"} <= await terms_of_source(session, "variable", "Office_phone")
    assert {"yoga", "orientation", "কাকরাইল"} <= await terms_of_source(session, "program", 1)

    qa = await session.get(QAPair, 1)
    qa.active = False                                                        # switched off -> not searchable
    await session.delete(await session.get(Variable, "Office_phone"))
    await session.commit()
    assert not await terms_of_source(session, "qa", 1) and not await terms_of_source(session, "variable", "Office_phone")

    await session.delete(await session.get(Document, d.id))                  # a deleted file takes its chunks' keywords along
    await session.commit()
    assert (await session.execute(select(func.count()).select_from(KeywordEntry).where(KeywordEntry.source_type == "chunk"))).scalar_one() == 0


async def test_search_picks_the_record_whose_keywords_match_and_ignores_the_rest(session, tmp_path):
    await _doc(session, tmp_path)
    res = await search_index(session, KeywordPlan(["Quantum", "Foundation", "saba", "prodhan"], "rules").keywords)
    assert res.matches
    top = (await session.get(Chunk, int(res.matches[0].ref)))
    assert top.question.startswith("কোয়ান্টাম ফাউন্ডেশন কী কী সেবা") and res.matches[0].coverage >= 0.6   # Banglish -> সেবা / প্রদান
    by_ref = {m.ref: m for m in res.matches}
    kid = (await session.execute(select(Chunk.id).where(Chunk.question.like("%ছেলেকে%")))).scalars().first()
    assert str(kid) not in by_ref or by_ref[str(kid)].coverage < res.matches[0].coverage * 0.6
    # a keyword nothing in the index has drags coverage down instead of being ignored
    assert (await search_index(session, ["আবহাওয়া", "রক্তদান"])).top_coverage < res.matches[0].coverage


def test_ai_plan_parsing_is_strict():
    p = parse_plan('```json\n{"intent": "services offered", "keywords": ["সেবা", "service", "[PHONE_1]", "x", 5]}\n```')
    assert p.source == "ai" and p.keywords == ["সেবা", "service"] and p.intent == "services offered"
    assert parse_plan("not json") is None and parse_plan('{"keywords": []}') is None and parse_plan('{"intent": "x"}') is None


async def test_the_ai_is_asked_only_when_the_free_keywords_are_not_enough(session, maker, tmp_path):
    await seed_llm(session, routes={"keywords": ("anthropic", "kw-m", None, None)})
    await _doc(session, tmp_path)
    fake = FakeLLM({"kw-m": json.dumps({"intent": "stop attraction", "keywords": ["প্রেম", "love", "আকর্ষণ"]}, ensure_ascii=False)})
    llm = LLMService(maker, fake)
    kw = lambda q: keyword_retrieve(session, llm, text=q, retrieval_text=q, history=[], channel="messenger",  # noqa: E731
                                    conversation_id=None, is_test=True)
    r = await kw("How can I get free from romantic attraction?")        # English: no free keyword matches the Bangla index
    # the record's text never says "প্রেম": it is found through the keywords the data file gives for it
    assert r.usable and r.debug["source"] == "ai" and "সুন্দর স্মৃতি" in r.hits[0].text and fake.models() == ["kw-m"]
    assert r.debug["intent"] == "stop attraction"

    fake.calls.clear()
    r = await kw("কোয়ান্টাম ফাউন্ডেশন কী কী সেবা দেয়?")                   # the free keywords already cover it: no AI call
    assert r.usable and r.debug["source"] == "rules" and fake.calls == []

    fake.script["kw-m"] = "sorry, I cannot do that"                      # garbage from the AI -> the free keywords are used
    r = await kw("How can I get free from romantic attraction?")
    assert r.debug["source"] == "rules" and "ai_note" in r.debug


async def test_details_of_every_kind_of_record_reach_the_answer_step(session, tmp_path):
    await _doc(session, tmp_path)
    session.add_all([Variable(key="Seba_fee", value="free", description="সেবার ফি"),
                     Program(name="Seba Camp", start_date=today_bd() + timedelta(days=3), place="Dhaka")])
    await session.commit()
    ask = lambda q: keyword_retrieve(session, LLMService(session.bind, FakeLLM()), text=q, retrieval_text=q, history=[],  # noqa: E731
                                     channel="messenger", conversation_id=None, is_test=True, allow_ai=False)
    r = await ask("সেবা ফি camp")
    assert any(e.startswith("VALUE — সেবার ফি") for e in r.extra) and any(e.startswith("PROGRAM Seba Camp") for e in r.extra)
    r = await ask("সেবা রক্তদান")
    assert r.hits and "রক্তদান, দাফন, ত্রাণ" in r.hits[0].text             # a Q&A record arrives whole, with its answer


async def test_pipeline_sends_the_matching_record_first_to_the_model(session, maker, tmp_path):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "keyword_ai_enabled", False)
    await _doc(session, tmp_path)
    fake = FakeLLM({"small": "রক্তদান ও দাফনসহ নানা সেবা দেয়।"})
    res = await run_pipeline(session, LLMService(maker, fake), text="কোয়ান্টাম ফাউন্ডেশন কী কী সেবা দেয়?", profile=None,
                             conversation=None, channel="messenger", persist=False)
    assert res.path == "llm" and res.debug["keyword_search"]["used"] is True
    prompt = json.dumps(res.debug["prompt"], ensure_ascii=False)
    assert "রক্তদান, দাফন, ত্রাণ" in prompt                                  # the matching record, complete
    # other candidates may follow (the model judges them), but the keyword match leads the context
    others = [prompt.find(w) for w in ("প্রশান্ত", "সুন্দর স্মৃতি") if w in prompt]
    assert all(prompt.find("রক্তদান, দাফন, ত্রাণ") < i for i in others)


@pytest.mark.parametrize("model_says", ["NO_ANSWER", "CONTEXT-এ এই বিষয়ে কোনো তথ্য নেই।"])
async def test_the_model_judges_the_candidates_and_no_answer_is_the_standard_reply(session, maker, tmp_path, model_says):
    from app.models import UnansweredQuestion
    from app.services.settings_service import DEFAULTS
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "keyword_ai_enabled", False)
    await _doc(session, tmp_path)
    fake = FakeLLM({"small": model_says})
    res = await run_pipeline(session, LLMService(maker, fake), text="কোয়ান্টাম ফাউন্ডেশনের অফিসে এসি আছে?", profile=None,
                             conversation=None, channel="messenger")
    assert fake.models() == ["small"]                                       # candidates were found: the model judged them
    assert "ANSWER CHECK" in fake.calls[0][1].audit_json()                 # the rule sits right before the question
    assert res.path == "no_context" and res.text in (DEFAULTS["no_answer_reply"], DEFAULTS["no_answer_reply_en"])
    assert "CONTEXT" not in res.text
    assert (await session.execute(select(func.count()).select_from(UnansweredQuestion))).scalar_one() == 1


async def test_a_devanagari_digit_in_a_real_number_does_not_block_the_answer(session, maker, tmp_path):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)})
    await set_setting(session, "keyword_ai_enabled", False)
    recs = [{"question": "দাফন সেবার নম্বর কত?", "answer": "দাফন সেবা: +৮৮ ০১৩১৩ ৪৮৬৫০৯", "keywords": "দাফন, নম্বর"}]
    await _doc(session, tmp_path, recs)
    fake = FakeLLM({"small": "দাফন সেবার নম্বর: +৮৮ ০১৩১৩ ४৮৬৫০৯"})          # one Devanagari ৪ (४) slipped in
    res = await run_pipeline(session, LLMService(maker, fake), text="দাফন সেবার নম্বর কত?", profile=None,
                             conversation=None, channel="messenger", persist=False)
    assert res.path == "llm" and res.text == "দাফন সেবার নম্বর: +৮৮ ০১৩১৩ ৪৮৬৫০৯"


async def test_a_vanished_record_is_ignored_and_a_rebuild_cleans_up(session, tmp_path):
    d = await _doc(session, tmp_path)
    await session.execute(delete(Chunk).where(Chunk.document_id == d.id))   # bulk delete: no ORM event, orphans stay behind
    await session.commit()
    assert (await session.execute(select(func.count()).select_from(KeywordEntry))).scalar_one() > 0
    r = await keyword_retrieve(session, LLMService(session.bind, FakeLLM()), text="সেবা রক্তদান", retrieval_text="সেবা রক্তদান", history=[],
                               channel="messenger", conversation_id=None, is_test=True, allow_ai=False)
    assert not r.usable and not r.hits
    counts = await rebuild_index(session)
    assert counts["chunk"] == 0 and (await session.execute(select(func.count()).select_from(KeywordEntry))).scalar_one() == 0


async def test_rebuild_and_first_start_build(session, tmp_path):
    await _doc(session, tmp_path)
    await session.execute(delete(KeywordEntry))
    await session.commit()
    assert await ensure_index(session) is True and await ensure_index(session) is False   # builds once, then leaves it alone
    st = await stats(session)
    assert st["records"]["chunk"] == 3 and st["keywords"] > 10 and st["rebuilt_at"]


async def test_keyword_admin_api(client, admin, agent, session, tmp_path):  # noqa: F811
    await _doc(session, tmp_path)
    assert (await client.get("/api/keywords/stats", headers=agent)).json()["records"]["chunk"] == 3
    assert (await client.post("/api/keywords/rebuild", headers=agent)).status_code == 403
    assert (await client.post("/api/keywords/rebuild", headers=admin)).json()["records"]["chunk"] == 3
    r = (await client.get("/api/keywords/search", headers=admin, params={"q": "কোয়ান্টাম ফাউন্ডেশন কী কী সেবা দেয়?"})).json()
    assert r["usable"] and "রক্তদান" in r["records"][0]["text"] and r["debug"]["source"] == "rules"


def test_question_kinds():
    from app.rag.keyword_search import question_kind
    assert question_kind("Course fee koto?") == "amount" and question_kind("কোর্স ফি কত?") == "amount"
    assert question_kind("কিভাবে ভর্তি হবো?") == "how" and question_kind("কসমো স্কুল কোথায়?") == "where"
    assert question_kind("পরের ব্যাচ কবে?") == "when" and question_kind("গুরুজী কে?") is None


async def test_of_two_equal_records_the_one_answering_the_kind_of_question_wins(session, tmp_path):
    await _doc(session, tmp_path, [
        {"question": "কোর্স ফি কীভাবে দিতে হয়?", "answer": "বিকাশে পাঠান।"},
        {"question": "কোয়ান্টাম মেথড কোর্সের ফি কত?", "answer": "১১,৫০০ টাকা।"}], title="fees")
    r = await keyword_retrieve(session, LLMService(session.bind, FakeLLM()), text="Course fee koto?", retrieval_text="Course fee koto?",
                               history=[], channel="messenger", conversation_id=None, is_test=True, allow_ai=False)
    assert r.usable and "১১,৫০০" in r.hits[0].text                          # "how much" -> the amount, not the way to pay
    r = await keyword_retrieve(session, LLMService(session.bind, FakeLLM()), text="কোর্স ফি কিভাবে দেব", retrieval_text="কোর্স ফি কিভাবে দেব",
                               history=[], channel="messenger", conversation_id=None, is_test=True, allow_ai=False)
    assert "বিকাশে" in r.hits[0].text
