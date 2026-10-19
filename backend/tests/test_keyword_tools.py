import json

import pytest
from sqlalchemy import select

from app.llm.service import LLMService
from app.models.kb import DIM
from app.models import KeywordEntry, QAPair, RecordKeywords
from app.rag.keyword_gen import generate_keywords, grounded, parse_ai_keywords, stem_of
from app.rag.keyword_index import rebuild_index
from app.rag.keyword_tools import ai_generate, edit_record, keyword_tree, list_records, look_alike_report, make_unique, preview
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api2 import admin, agent  # noqa: F401 - fixtures


async def two_alike(session):
    a = QAPair(question="Quantum Method course fee for Dhaka batch", question_norm="quantum method course fee for dhaka batch", answer_template="The Dhaka batch fee is 5000 taka. Seats are limited.", embedding=[0.0]*DIM)
    b = QAPair(question="Quantum Method course fee for Chittagong batch", question_norm="quantum method course fee for chittagong batch", answer_template="The Chittagong batch fee is 7000 taka. Seats are limited.", embedding=[0.0]*DIM)
    session.add_all([a, b])
    await session.commit()
    return a, b


def test_long_text_gets_more_keywords_than_short_text():
    short = generate_keywords(title="fee", meta=None, doc=None, body="The fee is 5000 taka.", df={}, n=10)
    body = " ".join(f"topic{i % 40} detail{i} word{i}" for i in range(300))
    long = generate_keywords(title="fee", meta=None, doc=None, body=body, df={}, n=10)
    assert len(long) > len(short)


def test_ai_keywords_must_come_from_the_record():
    text = "The Dhaka batch fee is 5000 taka"
    kept, rejected = parse_ai_keywords(json.dumps({"keywords": ["dhaka", "fee", "scholarship", "visa"]}), text)
    assert "dhaka" in kept and "fee" in kept
    assert "scholarship" in rejected and "visa" in rejected
    assert grounded("dhaka", {stem_of("dhaka")})


@pytest.mark.asyncio
async def test_look_alike_records_get_unique_keywords_and_edits_survive_a_rebuild(session):
    a, b = await two_alike(session)
    await rebuild_index(session)
    out = await make_unique(session, "qa", str(a.id))
    await session.commit()
    assert out["look_alikes"]
    assert any("dhaka" in w.lower() for w in out["unique"]) or out["unique"]
    await edit_record(session, "qa", str(a.id), add=["উপবৃত্তি"], remove=[], restore=[])
    await session.commit()
    await rebuild_index(session)
    stems = set((await session.execute(select(KeywordEntry.stem).where(KeywordEntry.source_ref == str(a.id), KeywordEntry.source_type == "qa"))).scalars())
    assert stem_of("উপবৃত্তি") in stems
    rows = await list_records(session, "qa", search=None, limit=10, offset=0)
    item = next(i for i in rows["items"] if i["ref"] == str(a.id))
    assert any(k["origin"] == "manual" for k in item["keywords"])


@pytest.mark.asyncio
async def test_removed_automatic_keyword_stays_removed(session):
    a, _ = await two_alike(session)
    await rebuild_index(session)
    await edit_record(session, "qa", str(a.id), add=[], remove=[stem_of("taka")], restore=[])
    await session.commit()
    await rebuild_index(session)
    stems = set((await session.execute(select(KeywordEntry.stem).where(KeywordEntry.source_ref == str(a.id), KeywordEntry.source_type == "qa"))).scalars())
    assert stem_of("taka") not in stems


@pytest.mark.asyncio
async def test_ai_generate_stores_only_grounded_keywords(session, maker):
    a, _ = await two_alike(session)
    await rebuild_index(session)
    await seed_llm(session, routes={"keygen": ("anthropic", "kg", None, None)})
    llm = LLMService(maker, FakeLLM({"kg": json.dumps({"keywords": ["dhaka", "seats", "hallucinated-thing"]})}))
    res = await ai_generate(session, llm, "qa", str(a.id))
    await session.commit()
    assert "hallucinated-thing" in res["rejected"]
    row = (await session.execute(select(RecordKeywords).where(RecordKeywords.source_ref == str(a.id)))).scalar_one()
    assert row.ai_at is not None
    assert all(x["k"] != "hallucinated-thing" for x in row.added)


@pytest.mark.asyncio
async def test_preview_tree_and_report(session):
    await two_alike(session)
    await rebuild_index(session)
    kws = await preview(session, "Quantum Method course fee", "The fee is 5000 taka")
    assert kws and all({"term", "origin", "weight", "shared"} <= set(k) for k in kws)
    tree = await keyword_tree(session)
    assert tree["roots"] if "roots" in tree else tree
    assert isinstance(await look_alike_report(session), list)


@pytest.mark.asyncio
async def test_keyword_tool_api(client, admin, agent, session):  # noqa: F811
    a, _ = await two_alike(session)
    await rebuild_index(session)
    r = await client.get("/api/keywords/records?type=qa", headers=agent)
    assert r.status_code == 200 and r.json()["items"]
    assert (await client.put(f"/api/keywords/records/qa/{a.id}", json={"add": ["নতুন"]}, headers=agent)).status_code == 403
    assert (await client.put(f"/api/keywords/records/qa/{a.id}", json={"add": ["নতুন"]}, headers=admin)).status_code == 200
    assert (await client.get("/api/keywords/records?type=bogus", headers=admin)).status_code == 404
    g = await client.post(f"/api/keywords/records/qa/{a.id}/generate", json={}, headers=admin)
    assert g.status_code == 200
    assert (await client.post(f"/api/keywords/records/qa/{a.id}/generate", json={"ai": True}, headers=admin)).status_code == 409
    assert (await client.post("/api/keywords/preview", json={"question": "course fee"}, headers=agent)).status_code == 200
    assert (await client.get("/api/keywords/tree", headers=agent)).status_code == 200
    assert (await client.get("/api/keywords/look-alikes", headers=admin)).status_code == 200
    assert (await client.get("/api/keywords/generate-ai/status", headers=agent)).json()["status"] == "idle"
