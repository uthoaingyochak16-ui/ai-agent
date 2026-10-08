import json

import httpx
import respx
from sqlalchemy import select

from app.models import Document, Message, Profile
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.services import consent as C
from app.services.ingest import handle_message
from app.services.settings_service import set_setting
from tests.conftest import configure_channel, mk_msg, seed_llm
from tests.helpers import FakeLLM

GRAPH = "https://graph.facebook.com/v21.0/me/messages"


@respx.mock
async def test_message_to_kb_answer_to_consent_to_saved_phone(session, tmp_path, monkeypatch):
    st = LocalStorage(str(tmp_path))
    await configure_channel(session)
    await seed_llm(session, routes={t: ("anthropic", "small", None, None) for t in ("answer", "answer_large", "extraction", "summarization")})
    for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0)):
        await set_setting(session, k, v)
    d = Document(title="Hours", file_path=st.save("h.txt", b"Our shop is open from 9 am to 5 pm, Saturday to Thursday."), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=(HashingEmbedder(1024), None))
    fake = FakeLLM({"small": "We are open 9 am to 5 pm, Saturday to Thursday."})
    monkeypatch.setattr("app.llm.service.default_factory", fake)
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "out"}))

    await handle_message(session, mk_msg(text="what are the shop opening hours? my phone is 01712345678", mid="e1"))
    replies = [json.loads(c.request.content)["message"]["text"] for c in route.calls if b'"message"' in c.request.content]
    assert replies == ["We are open 9 am to 5 pm, Saturday to Thursday."]        # 1st message: answer only, no consent prompt yet
    sent = json.dumps(fake.calls[0][1].audit_payload)
    assert "01712345678" not in sent and "[PHONE_1]" in sent                       # nothing personal left the building

    await handle_message(session, mk_msg(text="and are you open on friday, what are the shop hours", mid="e2"))
    replies = [json.loads(c.request.content)["message"]["text"] for c in route.calls if b'"message"' in c.request.content]
    assert len(replies) == 2 and "may I save your name" in replies[1]              # consent asked once, after the answer
    p = (await session.execute(select(Profile))).scalar_one()
    assert C.consent_state(p) == "asked" and p.phone_enc is None

    await handle_message(session, mk_msg(text="yes", mid="e3"))
    await session.refresh(p)
    assert p.consent_given and C.get_phone(p) == "+8801712345678"
    await handle_message(session, mk_msg(text="what are the shop hours again", mid="e4"))
    replies = [json.loads(c.request.content)["message"]["text"] for c in route.calls if b'"message"' in c.request.content]
    assert sum("may I save your name" in r for r in replies) == 1                  # never asked a second time
    msgs = (await session.execute(select(Message).where(Message.role == "assistant"))).scalars().all()
    assert msgs[0].sources_json["path"] == "llm" and msgs[0].sources_json["chunks"][0]["document"] == "Hours"


@respx.mock
async def test_consent_is_not_requested_after_a_bare_greeting(session):
    from app.models import QAPair
    from app.rag.templates import normalize_question
    await configure_channel(session)
    vec = (await HashingEmbedder(1024).embed(["Hi"]))[0]
    session.add(QAPair(question="Hi", question_norm=normalize_question("Hi"), answer_template="Hello!", category="greeting", embedding=vec))
    await session.commit()
    route = respx.post(GRAPH).mock(return_value=httpx.Response(200, json={"message_id": "x"}))
    await handle_message(session, mk_msg(text="hi", mid="g1"))
    await handle_message(session, mk_msg(text="Hi", mid="g2"))          # 2nd message, still just a greeting
    replies = [json.loads(c.request.content)["message"]["text"] for c in route.calls if b'"message"' in c.request.content]
    from app.services.settings_service import DEFAULTS
    assert replies == [DEFAULTS["greeting_first_reply"], DEFAULTS["greeting_repeat_reply"]]  # welcome, then a short "go ahead"
    p = (await session.execute(select(Profile))).scalar_one()
    assert C.consent_state(p) == "unknown"
