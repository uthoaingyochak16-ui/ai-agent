from sqlalchemy import select

from app.models import SemanticCache, Variable
from app.rag.embeddings import HashingEmbedder, embedding_signature
from app.services.answer_cache import same_topic
from app.services.pipeline import run_pipeline
from app.services.reply_engine import get_llm_service
from app.services.settings_service import get_setting, set_setting
from tests.test_admin_api2 import admin  # noqa: F401 - fixture

EMB = (HashingEmbedder(1024), None)


def test_topic_words_must_match():
    assert same_topic("course fee koto?", "Course fee koto")
    assert same_topic("course fee koto?", "কোর্স ফি কত?")                 # synonyms across scripts
    assert same_topic("Quantum course fee koto?", "course fee koto?")      # organisation name is neutral
    assert not same_topic("yoga course fee koto?", "course fee koto?")     # one more topic word: different question
    assert not same_topic("course fee koto?", "course er schedule?")
    assert not same_topic("ki?", "kemon?")                                 # nothing to compare: never trust it


async def add_entry(session, question, answer, *, status="auto", ctx=None):
    vec = (await EMB[0].embed([question]))[0]
    c = SemanticCache(question=question, answer=answer, embedding=vec, status=status,
                      embedding_version=await embedding_signature(session, embedder=EMB),
                      context_version=ctx if ctx is not None else int(await get_setting(session, "context_version") or 1))
    session.add(c)
    await session.commit()
    return c


async def ask(session, text):
    return await run_pipeline(session, get_llm_service(session), text=text, profile=None, conversation=None,
                              channel="messenger", persist=False, embedder=EMB)


async def test_close_vector_with_different_topic_is_not_served(session):
    await set_setting(session, "cache_threshold", 0.5)   # make the vector check pass so only the topic rule decides
    await session.commit()
    await add_entry(session, "course fee koto?", "কোর্স ফি ১১,৫০০ টাকা")
    assert (await ask(session, "course fee koto?")).path == "cache"
    res = await ask(session, "yoga course fee koto?")
    assert res.path != "cache" and any("different topic" in s for s in res.debug["steps"])


async def test_blocked_never_served_and_verified_survives_knowledge_changes(session):
    blocked = await add_entry(session, "office kothay?", "wrong answer", status="blocked")
    assert (await ask(session, "office kothay?")).path != "cache"
    await session.delete(blocked)
    await add_entry(session, "office kothay?", "ঢাকার শান্তিনগরে", status="verified")
    await add_entry(session, "phone number koto?", "017...", status="auto")
    await set_setting(session, "context_version", int(await get_setting(session, "context_version") or 1) + 1)
    await session.commit()
    assert (await ask(session, "office kothay?")).text == "ঢাকার শান্তিনগরে"     # verified: kept
    assert (await ask(session, "phone number koto?")).path != "cache"            # auto: expired with the knowledge


async def test_wrong_answer_feedback_blocks_copies_and_can_clear_all(client, admin, session):  # noqa: F811
    await add_entry(session, "fee koto?", "BAD")
    await add_entry(session, "course fee koto?", "BAD")
    keep_id = (await add_entry(session, "schedule?", "OK")).id
    r = (await client.post("/api/cache/feedback", headers=admin, json={"answer": "BAD"})).json()
    assert r == {"blocked": 2, "cleared_all": False}
    statuses = {c.answer: c.status for c in (await session.execute(select(SemanticCache))).scalars()}
    assert statuses == {"BAD": "blocked", "OK": "auto"}
    before = int(await get_setting(session, "context_version") or 1)
    await client.post("/api/cache/feedback", headers=admin, json={"answer": "OK", "clear_all": True})
    session.expire_all()
    assert int(await get_setting(session, "context_version")) == before + 1

    lst = (await client.get("/api/cache", headers=admin, params={"status": "blocked"})).json()
    assert lst["total"] == 3
    edited = (await client.put(f"/api/cache/{keep_id}", headers=admin, json={"answer": "Corrected"})).json()
    assert edited["status"] == "verified" and edited["answer"] == "Corrected"
    assert (await client.delete(f"/api/cache/{keep_id}", headers=admin)).status_code == 200


async def test_changing_a_variable_forgets_answers_that_quote_its_old_value(client, admin, session):  # noqa: F811
    session.add(Variable(key="Course_fee", value="11500"))
    await session.commit()
    await add_entry(session, "fee koto?", "কোর্স ফি 11500 টাকা", status="verified")
    await add_entry(session, "dam koto?", "দাম 115000 টাকা")          # a different number that merely contains it
    await client.put("/api/variables/Course_fee", headers=admin, json={"value": "12000"})
    session.expire_all()
    left = [c.answer for c in (await session.execute(select(SemanticCache))).scalars()]
    assert left == ["দাম 115000 টাকা"]
