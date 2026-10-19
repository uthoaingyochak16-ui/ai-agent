from sqlalchemy import select

from app.models import ChannelConfig, Chunk, Document, QAPair, SemanticCache
from app.rag.embeddings import HashingEmbedder, embedding_signature
from app.services.embedding_maintenance import embedding_status, reembed_all
from app.services.settings_service import get_setting, set_setting


async def vector(text="x"):
    return (await HashingEmbedder(1024).embed([text]))[0]


async def seed_vectors(session, version="old:model:1024"):
    doc = Document(title="D", file_path="d.txt", type="txt", status="ready", chunk_count=1)
    session.add(doc)
    await session.flush()
    session.add(Chunk(document_id=doc.id, position=0, content="বাংলা তথ্য", embedding=await vector("chunk"),
                      embedding_version=version))
    session.add(QAPair(question="fee?", question_norm="fee", answer_template="10", embedding=await vector("qa"),
                       embedding_version=version))
    session.add(SemanticCache(question="cached?", answer="yes", embedding=await vector("cache"),
                              embedding_version=version, context_version=1))
    await session.commit()


async def test_mismatch_detection_counts_each_vector_table(session):
    await seed_vectors(session)
    state = await embedding_status(session)
    assert state["mismatch"] == {"chunks": 1, "qa": 1, "cache": 1}
    assert state["mismatch_total"] == 3


async def test_reembed_job_updates_every_row_and_bumps_context(maker):
    async with maker() as session:
        await seed_vectors(session)
        before = int(await get_setting(session, "context_version") or 1)
    result = await reembed_all(maker)
    assert result == {"status": "done", "done": 3, "total": 3}
    async with maker() as session:
        signature = await embedding_signature(session)
        assert set((await session.execute(select(Chunk.embedding_version))).scalars()) == {signature}
        assert set((await session.execute(select(QAPair.embedding_version))).scalars()) == {signature}
        assert set((await session.execute(select(SemanticCache.embedding_version))).scalars()) == {signature}
        assert int(await get_setting(session, "context_version")) == before + 1


async def test_hash_warning_requires_hash_and_a_real_enabled_channel(session):
    assert (await embedding_status(session))["hash_warning"] is False
    session.add(ChannelConfig(channel="messenger", enabled=False, status="unconfigured"))
    await session.commit()
    assert (await embedding_status(session))["hash_warning"] is False
    channel = (await session.execute(select(ChannelConfig))).scalar_one()
    channel.enabled = True
    await session.commit()
    assert (await embedding_status(session))["hash_warning"] is True
    await set_setting(session, "embedding_provider", "openai")
    await session.commit()
    assert (await embedding_status(session))["hash_warning"] is False
