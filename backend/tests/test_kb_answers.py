import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.rag.categories import CATEGORIES
from app.models import Chunk, Conversation, Document, Message, QAPair
from app.rag.documents import LocalStorage, chunk_sections, process_document
from app.rag.embeddings import HashingEmbedder
from app.rag.extract import extract_sections
from app.rag.search import shorten
from app.services.handover import maybe_auto_resume, pause_ai
from app.services.ingest import handle_message
from app.services.pipeline import run_pipeline
from app.services.reply_engine import get_llm_service
from tests.conftest import mk_msg
from tests.test_admin_api2 import admin  # noqa: F401 - fixture

EMB = (HashingEmbedder(1024), None)
LONG = ("কোয়ান্টাম হার্ট ক্লাবের যাত্রা শুরু হয় ২০০৯ সালে। গত ১০ বছরে বহু হৃদরোগী জীবনধারা বদলে সুস্থ হয়েছেন। "
        + "মেডিটেশন, সঠিক খাদ্যাভ্যাস ও নিয়মিত হাঁটা এর মূল অংশ। " * 30)
RECORDS = [
    {"id": "hc1", "topic": "শুরুর কথা", "page": 13,
     "question": "কোয়ান্টাম হার্ট ক্লাবের জীবনধারা মেনে চলে কি সত্যিই হৃদরোগীরা সুস্থ হয়েছেন?", "answer": LONG},
    {"id": "hc2", "topic": "খাদ্য", "page": 20, "question": "হৃদরোগীরা কি ডিম খেতে পারবেন?",
     "answer": "ডিমের কুসুম এড়িয়ে চলুন, সাদা অংশ খাওয়া যায়। সপ্তাহে ২-৩টি ডিমের সাদা অংশ যথেষ্ট।"},
    {"id": "hc3", "topic": "ব্যায়াম", "page": 31, "question": "প্রতিদিন কতক্ষণ হাঁটা উচিত?",
     "answer": "প্রতিদিন অন্তত ৩০ থেকে ৪৫ মিনিট দ্রুত হাঁটা উচিত।"},
]


def test_json_qa_records_become_titled_small_chunks():
    secs = extract_sections(json.dumps(RECORDS, ensure_ascii=False).encode(), "json")
    assert len(secs) == 3 and secs[1].title == RECORDS[1]["question"] and secs[1].page == 20
    assert "খাদ্য" not in secs[1].text and "hc2" not in secs[1].text  # topic / id are bookkeeping, not answer text
    chunks = chunk_sections(secs, target=60, overlap=10)
    long_parts = [c for c in chunks if c.question == RECORDS[0]["question"]]
    assert len(long_parts) > 3                                   # the long answer is cut small...
    assert all(c.content.startswith("প্রশ্ন: কোয়ান্টাম হার্ট") for c in long_parts)  # ...and each piece keeps its question
    assert all(len(c.content.split()) < 120 for c in chunks)


def test_csv_rows_are_separate_sections():
    csv = "question,answer\nফি কত?,ফি ১১৫০০ টাকা।\nকোথায়?,ঢাকায়।\n".encode()
    secs = extract_sections(csv, "csv")
    assert [s.title for s in secs] == ["ফি কত?", "কোথায়?"]


def test_shorten_keeps_whole_sentences():
    out = shorten(LONG, 200)
    assert len(out) <= 200 and out.endswith("।")


async def _doc(session, tmp_path):
    st = LocalStorage(str(tmp_path))
    d = Document(title="heart club", file_path=st.save("hc.json", json.dumps(RECORDS, ensure_ascii=False).encode()), type="json")
    session.add(d)
    await session.commit()
    assert await process_document(session, d.id, storage=st, embedder=EMB) == "ready"
    return d


async def test_question_from_the_file_gets_a_short_accurate_answer_without_an_ai_model(session, tmp_path):
    await _doc(session, tmp_path)
    llm = get_llm_service(session)  # no provider configured
    res = await run_pipeline(session, llm, text="কোয়ান্টাম হার্ট ক্লাবের জীবনধারা মেনে চলে কি সত্যিই হৃদরোগীরা সুস্থ হয়েছেন?",
                             profile=None, conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path == "kb_extract"
    assert res.text.startswith("কোয়ান্টাম হার্ট ক্লাবের যাত্রা") and len(res.text) <= 450

    res = await run_pipeline(session, llm, text="হৃদরোগী ডিম খেতে পারবে?", profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert res.path == "kb_extract" and "কুসুম" in res.text

    res = await run_pipeline(session, llm, text="প্রতিদিন কতক্ষণ হাঁটবো", profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert "৩০ থেকে ৪৫ মিনিট" in res.text

    res = await run_pipeline(session, llm, text="course fee koto?", profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert res.path == "no_context"  # unrelated question: no made-up answer from a random chunk


async def test_qa_near_identical_wording_matches(session):
    vec = (await HashingEmbedder(1024).embed(["x"]))[0]
    session.add(QAPair(question="কোর্স ফি কত?", question_norm="কোর্স ফি কত", answer_template="কোর্স ফি {fee} টাকা।", embedding=vec))
    from app.models import Variable
    session.add(Variable(key="fee", value="11500"))
    await session.commit()
    res = await run_pipeline(session, get_llm_service(session), text="কোর্স ফি কতো", profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert res.path == "qa_shortcut" and res.text == "কোর্স ফি 11500 টাকা।"


async def test_qa_short_question_matches_when_brand_is_added(session):
    vec = (await HashingEmbedder(1024).embed(["x"]))[0]
    session.add(QAPair(question="Course fee koto?", question_norm="course fee koto",
                       answer_template="{Course_fee}", category="fee", embedding=vec, active=True))
    from app.models import Variable
    session.add(Variable(key="Course_fee", value="11500"))
    await session.commit()
    res = await run_pipeline(session, get_llm_service(session), text="Quantum method course fee koto?", profile=None,
                             conversation=None, channel="messenger", persist=False, embedder=EMB)
    assert res.path == "qa_shortcut" and res.text == "11500"


async def test_qa_import_json_and_chunk_editing(client, admin, session, tmp_path, monkeypatch):  # noqa: F811
    files = {"file": ("faq.json", json.dumps(RECORDS, ensure_ascii=False).encode(), "application/json")}
    r = await client.post("/api/qa-import", headers=admin, files=files)
    assert r.status_code == 200 and r.json()["created"] == 3
    items = (await client.get("/api/qa", headers=admin)).json()["items"]
    assert {i["category"] for i in items} <= set(CATEGORIES)  # free topics -> a standard category, guessed from the question

    monkeypatch.setattr("app.rag.documents.get_storage", lambda: LocalStorage(str(tmp_path)))
    d = await _doc(session, tmp_path)
    ch = (await client.get(f"/api/documents/{d.id}/chunks", headers=admin)).json()
    first = ch["chunks"][0]
    assert first["question"] and ch["total"] == d.chunk_count
    upd = await client.put(f"/api/documents/{d.id}/chunks/{first['id']}", headers=admin,
                           json={"content": "সংশোধিত লেখা", "question": first["question"]})
    assert upd.status_code == 200 and upd.json()["content"] == "সংশোধিত লেখা"
    added = (await client.post(f"/api/documents/{d.id}/chunks", headers=admin, json={"content": "নতুন তথ্য: অফিস ঢাকায়।"})).json()
    assert (await client.delete(f"/api/documents/{d.id}/chunks/{added['id']}", headers=admin)).status_code == 200
    assert (await client.post("/api/documents/reprocess-all", headers=admin)).json()["queued"] == 1


async def test_ai_comes_back_after_staff_go_quiet(maker):
    async with maker() as s:
        r = await handle_message(s, mk_msg(uid="test:pause", text="hi", mid="p1"))
        await pause_ai(s, r.conversation, "agent:staff@x")
        await s.commit()
        assert not await maybe_auto_resume(s, r.conversation, 30)  # staff just replied
        r.conversation.ai_paused_at = datetime.now(timezone.utc) - timedelta(minutes=31)
        assert await maybe_auto_resume(s, r.conversation, 30) and not r.conversation.ai_paused
        await s.commit()
    async with maker() as s:  # paused long ago -> the next message is answered again
        conv = (await s.execute(select(Conversation))).scalar_one()
        conv.ai_paused, conv.ai_paused_at = True, datetime.now(timezone.utc) - timedelta(hours=2)
        await s.commit()
        await handle_message(s, mk_msg(uid="test:pause", text="who are you", mid="p2"))
        last = (await s.execute(select(Message).order_by(Message.id.desc()).limit(1))).scalar_one()
        assert last.role == "assistant"


def test_synonyms_and_rare_words():
    from app.rag.search import content_words, coverage, weighted_coverage
    # English / Banglish / Bangla synonyms count as the same word
    assert coverage(["register", "course"], "কোর্সে রেজিস্ট্রেশন করতে ফর্ম পূরণ করুন")[0] == 2
    assert coverage(["ভর্তি"], "অনলাইন রেজিস্ট্রেশন ফর্ম")[0] == 1
    # a missing rare word (the city) sinks a chunk about another branch
    idf = {"সিলেট": 5.0, "রংপুর": 5.0, "শাখা": 1.0, "ঠিকানা": 1.0}
    rangpur = "রংপুর শাখা ঠিকানা: বাসা ১, রোড ৪"
    assert weighted_coverage(["সিলেট", "শাখা", "ঠিকানা"], rangpur, idf, 5.0) < 0.35
    assert weighted_coverage(["রংপুর", "শাখা", "ঠিকানা"], rangpur, idf, 5.0) == 1.0
    # Conversational Banglish filler must not dilute an otherwise exact entity match.
    assert content_words("Quantum ki Heart club niya kaj kore?") == ["quantum", "heart", "club"]


def test_variable_matching():
    from app.models import Variable
    from app.rag.variables import match_variable, variable_answer
    fee = Variable(key="Course_fee", value="11500")
    yoga = Variable(key="Yoga_fee", value="3000", description="কোয়ান্টাম ইয়োগা কোর্সের ফি")
    phone = Variable(key="Office_phone", value="01714974333")
    vs = [fee, yoga, phone]
    for q in ("Course fee koto?", "কোর্স ফি কত?", "What is the course fee?", "quantum course er fee koto"):
        assert match_variable(q, vs)[0] is fee, q
    assert match_variable("ইয়োগা কোর্সের ফি কত?", vs)[0] is yoga
    assert match_variable("office phone number den", vs)[0] is phone
    assert match_variable("meditation course e ki shekhay?", vs) is None   # not a value question
    assert match_variable("heart club course fee koto", vs) is None        # "heart club" is not what Course_fee is
    assert variable_answer(fee) == "Course fee: 11500" and variable_answer(yoga) == "কোয়ান্টাম ইয়োগা কোর্সের ফি: 3000"


async def test_variable_answers_before_documents_and_api_keeps_description(client, admin, session):  # noqa: F811
    r = await client.put("/api/variables/Course_fee", headers=admin, json={"value": "12000", "description": "কোর্সের ফি"})
    assert r.json()["description"] == "কোর্সের ফি"
    await client.put("/api/variables/Course_fee", headers=admin, json={"value": "12500"})  # description kept
    assert (await client.get("/api/variables", headers=admin)).json()[0] == {"key": "Course_fee", "value": "12500", "description": "কোর্সের ফি"}
    res = await run_pipeline(session, get_llm_service(session), text="Course fee koto?", profile=None, conversation=None,
                             channel="messenger", persist=False, embedder=EMB)
    assert res.path == "variable" and res.text == "কোর্সের ফি: 12500"


async def test_saved_question_plus_org_name_matches_but_another_subject_does_not(session):
    vec = (await HashingEmbedder(1024).embed(["x"]))[0]
    from app.models import Variable
    session.add(QAPair(question="Course fee koto?", question_norm="course fee koto", answer_template="{Course_fee} টাকা", embedding=vec))
    session.add(Variable(key="Course_fee", value="11500"))
    await session.commit()
    ask = lambda q: run_pipeline(session, get_llm_service(session), text=q, profile=None, conversation=None,  # noqa: E731
                                 channel="messenger", persist=False, embedder=EMB)
    assert (await ask("Quantum Method course fee koto?")).text == "11500 টাকা"
    assert (await ask("Yoga course fee koto?")).path != "qa_shortcut"


def test_banglish_sound_keys_match_bangla_words():
    from app.rag.translit import build_keymap, romanize, sound_key
    for latin, bangla in (("prem", "প্রেম"), ("valentine", "ভ্যালেন্টাইন"), ("islam", "ইসলাম"), ("palon", "পালন"),
                          ("valobasa", "ভালবাসা"), ("bhalobasha", "ভালোবাসা")):
        assert sound_key(latin) == sound_key(romanize(bangla)), (latin, bangla)
    km = build_keymap(["প্রেম", "পরম", "ইসলাম"])
    assert "প্রেম" in km[sound_key("prem")] and km[sound_key("islam")] == ["ইসলাম"]


def test_short_answer_is_the_answer_and_classification_fields_are_hidden():
    recs = [{"id": "qa_1", "topic": "প্রেম", "page": "13", "question_type": "definition", "question": "প্রেম কী?",
             "keywords": "['প্রেম']", "short_answer": "প্রেম স্বর্গীয়।", "details_ref": "details_001"}]
    sec = extract_sections(json.dumps(recs, ensure_ascii=False).encode(), "json")[0]
    assert sec.title == "প্রেম কী?" and sec.text == "প্রেম স্বর্গীয়।" and sec.page == 13


async def test_a_record_is_only_quoted_when_its_question_is_about_the_guests_words(session, tmp_path):
    st = LocalStorage(str(tmp_path))
    recs = [{"question": "আমার ছেলেকে কোয়ান্টাম মেথড কোর্স করিয়েছি, সে অস্থির। কী করব?", "answer": "আগে আপনাকে প্রশান্ত হতে হবে।"},
            {"question": "কোয়ান্টাম ফাউন্ডেশন কী কী সেবা দেয়?", "answer": "রক্তদান, দাফন, ত্রাণ ও শিক্ষা সেবা।"}]
    d = Document(title="qa", file_path=st.save("qa.json", json.dumps(recs, ensure_ascii=False).encode()), type="json")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=EMB)
    ask = lambda q: run_pipeline(session, get_llm_service(session), text=q, profile=None, conversation=None,  # noqa: E731
                                 channel="messenger", persist=False, embedder=EMB)
    assert "রক্তদান" in (await ask("Quantum Foundation ki ki saba prodhan kore thake?")).text
    res = await ask("কোয়ান্টাম কোর্সের ভর্তি কবে?")      # neither record's question is about admission dates
    assert "প্রশান্ত" not in res.text
