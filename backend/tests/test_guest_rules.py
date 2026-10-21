"""Reply language, location updates, fee / link rules and documents saved while the embedding provider is down."""
from datetime import date

from sqlalchemy import select

from app.llm.providers import LLMError
from app.models import Chunk, Document, Program
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.rag.programs import _sentence, match_program_question
from app.services.embedding_maintenance import embed_pending
from app.services.info_detect import split
from app.services.language import detect
from app.services.pipeline import FEE_AND_LINK_RULES, PROGRAM_RULES
from app.services.post_reply import EXTRACT_SYSTEM
from app.services.tools import TOOLS_PROMPT


def test_bangla_unless_the_guest_really_writes_english():
    for t in ("Agent", "ok", "Chottogram", "Course fee?", "Hello", "Ki obostha tomar?", "Songghayon program kobe hobe",
              "ami ekhon Sylhet e thaki", "Cumila te apnader office acha?"):
        assert detect(t) == "bn", t
    for t in ("What is the course fee?", "I want to contact your team members.", "When is the next program?"):
        assert detect(t) == "en", t


def test_a_district_alone_is_checked_for_the_guests_location():
    assert all(p.info for p in split("Chottogram"))
    assert all(p.info for p in split("ekhon Sylhet e"))
    assert "NEW place" in EXTRACT_SYSTEM and "corrects" in EXTRACT_SYSTEM


def _program(fee=None):
    return Program(id=1, name="সঙ্ঘায়ন", start_date=date(2026, 10, 21), place="কাকরাইল, ঢাকা", fee=fee)


def test_the_fee_only_when_asked_and_free_always():
    paid, free = _program("১১,৫০০ টাকা"), _program("ফ্রি")
    q = match_program_question("সঙ্ঘায়ন কবে?", [paid])
    assert q is not None and not q.wants_fee
    assert "১১,৫০০" not in _sentence(paid, q, date(2026, 10, 20), "bn")
    assert "ফ্রি" in _sentence(free, q, date(2026, 10, 20), "bn")            # free invites the guest
    q = match_program_question("সঙ্ঘায়ন ফি কত?", [paid])
    assert q.wants_fee and "১১,৫০০" in _sentence(paid, q, date(2026, 10, 20), "bn")


def test_answer_rules_hold_the_fee_and_link_back():
    assert "ONLY when the guest asks about the cost" in FEE_AND_LINK_RULES
    assert "ONLY when the guest shows they want to take part" in FEE_AND_LINK_RULES
    assert "give that link exactly" not in PROGRAM_RULES and "only then" in TOOLS_PROMPT


class _Down(HashingEmbedder):
    """The provider's quota is gone."""
    async def embed(self, texts):
        raise LLMError("429 exceeded your current quota", status=429)


async def test_a_document_is_saved_when_embedding_fails_and_completed_later(session, maker, tmp_path):
    st = LocalStorage(str(tmp_path))
    d = Document(title="Hajj guide", file_path=st.save("h.txt", "Hajj camp registration opens in May. ".encode() * 20),
                 type="txt")
    session.add(d)
    await session.commit()
    assert await process_document(session, d.id, storage=st, embedder=(_Down(1024), None)) == "ready"
    await session.refresh(d)
    assert d.status == "ready" and d.error.startswith("Meaning search pending")
    chunks = (await session.execute(select(Chunk).where(Chunk.document_id == d.id))).scalars().all()
    assert chunks and all(c.embedding_version is None for c in chunks)

    assert await embed_pending(maker) == len(chunks)                       # the provider works again (hash here)
    async with maker() as s:
        assert all(c.embedding_version for c in (await s.execute(select(Chunk))).scalars())
        assert (await s.get(Document, d.id)).error is None
    assert await embed_pending(maker) == 0
