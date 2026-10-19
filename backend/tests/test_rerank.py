import httpx
import respx

from app.core import crypto
from app.models import Chunk, Document, LLMProviderConfig
from app.rag.embeddings import HashingEmbedder, embedding_signature
from app.rag.rerank import rerank_texts
from app.rag.search import hybrid_search
from app.services.settings_service import set_setting


async def configure(session, name: str, base_url: str, *, key="secret", minimum=None):
    session.add(LLMProviderConfig(name=f"rerank_{name}", enabled=True, allow_pii=False, base_url=base_url,
                                  api_key_enc=crypto.encrypt(key, aad=f"rerank:{name}") if key else None))
    await set_setting(session, "rerank_provider", name)
    await set_setting(session, "rerank_model", "rerank-multilingual-v3.0")
    await set_setting(session, "rerank_min_score", minimum)
    await session.commit()


@respx.mock
async def test_cohere_reranker_redacts_text_and_parses_scores(session):
    await configure(session, "cohere", "https://api.cohere.test", minimum=0.25)
    route = respx.post("https://api.cohere.test/v2/rerank").mock(return_value=httpx.Response(200, json={
        "results": [{"index": 1, "relevance_score": 0.91}, {"index": 0, "relevance_score": 0.31}]
    }))
    ranked, minimum = await rerank_texts(session, "Call 01712345678 about fees",
                                         ["Phone 01712345678", "The course fee is 11,500 taka"])
    assert ranked == [(1, 0.91), (0, 0.31)] and minimum == 0.25
    sent = route.calls[0].request.content.decode()
    assert "01712345678" not in sent and "[PHONE_1]" in sent
    assert route.calls[0].request.headers["authorization"] == "Bearer secret"


async def seed_chunks(session):
    emb = HashingEmbedder(1024)
    signature = await embedding_signature(session)
    doc = Document(title="Fees", file_path="fees.txt", type="txt", status="ready", chunk_count=2)
    session.add(doc)
    await session.flush()
    texts = ["Quantum course fee is 10,000 taka old information",
             "Quantum course fee is 11,500 taka current information"]
    vectors = await emb.embed(texts)
    for i, (text, vector) in enumerate(zip(texts, vectors)):
        session.add(Chunk(document_id=doc.id, position=i, content=text, embedding=vector,
                          embedding_version=signature))
    await session.commit()
    return texts, await emb.embed(["Quantum course fee"])


@respx.mock
async def test_local_reranker_controls_final_order_score_and_threshold(session):
    texts, vectors = await seed_chunks(session)
    await configure(session, "local", "http://reranker.test", key="", minimum=0.5)
    route = respx.post("http://reranker.test/rerank").mock(return_value=httpx.Response(200, json=[
        {"index": 1, "score": 0.96}, {"index": 0, "score": 0.2}
    ]))
    hits = await hybrid_search(session, "Quantum course fee", vectors[0], top_k=3, min_similarity=0)
    assert len(hits) == 1 and hits[0].chunk.content == texts[1]
    assert hits[0].score == hits[0].rerank == 0.96
    body = route.calls[0].request.content.decode()
    assert '"texts"' in body and '"query":"Quantum course fee"' in body


@respx.mock
async def test_reranker_failure_falls_back_to_hybrid_scores(session):
    _, vectors = await seed_chunks(session)
    await configure(session, "cohere", "https://api.cohere.test")
    respx.post("https://api.cohere.test/v2/rerank").mock(return_value=httpx.Response(503, text="down"))
    hits = await hybrid_search(session, "Quantum course fee", vectors[0], top_k=3, min_similarity=0)
    assert len(hits) == 2 and all(hit.rerank is None for hit in hits)
    assert all(hit.score > 0 for hit in hits)
