import json

import pytest
from sqlalchemy import select

from app.llm.service import LLMService
from app.models import Document, FamilyMember, Profile, ProfileFact
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.services import consent as C
from app.services import contact_owner as co
from app.services.ingest import handle_message
from tests.conftest import mk_msg, seed_llm
from tests.helpers import FakeLLM


def owner_of(text):
    m = next(co.PHONE_CAND_RE.finditer(co.ascii_digits(text)))
    return co.classify(co.ascii_digits(text), m.start(), m.end())


@pytest.mark.parametrize("text,kind,relation", [
    ("amar number 01712345678", "self", None),
    ("01712345678 eta amar number", "self", None),
    ("amake call diben 01712345678", "self", None),
    ("আমার নম্বর ০১৭১২৩৪৫৬৭৮", "self", None),
    ("amar bhai er number 01712345678", "family", "brother"),
    ("ammur number 01712345678", "family", "mother"),
    ("আমার স্বামীর নম্বর 01712345678", "family", "husband"),
    ("01712345678 eta amar abbur number", "family", "father"),
    ("amar office er number 01712345678", "other", None),
    ("apnader 01712345678 number e call dilam keu dhore na", "other", None),
    ("amar bondhur number 01712345678", "other", None),
    ("doctor er chamber 01712345678", "other", None),
    ("amar number 01712345678, ami college e pori", "self", None),   # what comes first decides
    ("01712345678", "unknown", None),
])
def test_whose_number(text, kind, relation):
    o = owner_of(text)
    assert o.kind == kind and co.relation_en(o.relation) == relation


async def env(session, maker, tmp_path, reply=None):
    await seed_llm(session, routes={"extraction": ("anthropic", "ext", None, None)})
    st = LocalStorage(str(tmp_path))
    d = Document(title="Office", file_path=st.save("o.txt", "Quantum office: call 01714-974333 any day.".encode()), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=(HashingEmbedder(1024), None))
    fake = FakeLLM({"ext": json.dumps(reply or {})})
    return LLMService(maker, fake)


async def guest(session, llm, uid="test:own"):
    r = await handle_message(session, mk_msg(uid=uid, text="hello", mid=f"{uid}-0"), llm=llm)
    r.profile.consent_given = True
    await session.commit()
    return r


async def say(session, llm, text, mid, uid="test:own"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def phone(session):
    p = (await session.execute(select(Profile))).scalar_one()
    await session.refresh(p)
    return C.get_phone(p)


async def test_our_own_office_number_is_never_the_guests(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path, {"contacts": [{"ref": "[PHONE_1]", "owner": "self"}]})  # even if the AI errs
    await guest(session, llm)
    await say(session, llm, "01714974333 e call dilam keu dhorlo na", "a1")
    assert await phone(session) is None


async def test_a_relatives_number_goes_on_the_relative(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path)
    await guest(session, llm)
    await say(session, llm, "amar bhai er number 01812345679, oke call diben", "b1")
    assert await phone(session) is None
    fam = (await session.execute(select(FamilyMember))).scalar_one()
    assert (fam.relation, fam.details) == ("brother", {"phone": "+8801812345679"})


async def test_someone_elses_number_is_not_kept(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path, {"contacts": [{"ref": "[PHONE_1]", "owner": "other"}]})
    await guest(session, llm)
    await say(session, llm, "amar bondhur number 01812345679", "c1")
    assert await phone(session) is None
    assert not (await session.execute(select(FamilyMember))).first()


async def test_the_guests_own_number_is_saved(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path)
    await guest(session, llm)
    await say(session, llm, "amar number 01812345679, call diben", "d1")
    assert await phone(session) == "+8801812345679"


async def test_an_unclear_number_is_left_to_the_ai(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path, {"contacts": [{"ref": "[PHONE_1]", "owner": "self"}]})
    await guest(session, llm)
    await say(session, llm, "kal theke 01812345679 ei number e whatsapp kora jabe, ami Dhaka te thaki", "e1")
    assert await phone(session) == "+8801812345679"                         # the AI said it is theirs


async def test_an_unclear_number_the_ai_calls_an_organisation_is_dropped(session, maker, tmp_path):
    llm = await env(session, maker, tmp_path, {"contacts": [{"ref": "[PHONE_1]", "owner": "organization"}]})
    await guest(session, llm)
    await say(session, llm, "kal theke 01812345679 ei number e whatsapp kora jabe, ami Dhaka te thaki", "f1")
    assert await phone(session) is None
    facts = {f.key for f in (await session.execute(select(ProfileFact))).scalars()}
    assert "merge_idx_phone" not in facts                                    # not even remembered for linking


@pytest.mark.parametrize("text,kind,relation", [
    ("amar bhai Rakib er number 01812345690", "family", "brother"),
    ("আমার ভাই রাকিবের নম্বর ০১৮১২৩৪৫৬৯০", "family", "brother"),
    ("amar ma ar ami course korbo, amar number 01812345690", "self", None),
    ("kal theke 01712345692 ei number e whatsapp e pawa jabe amake", "self", None),
    ("my mother's number is 01712345692", "family", "mother"),
])
def test_whose_number_harder(text, kind, relation):
    o = owner_of(text)
    assert o.kind == kind and co.relation_en(o.relation) == relation
