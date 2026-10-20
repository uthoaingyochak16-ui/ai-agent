import dataclasses
import json
from datetime import datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from app.llm.providers import AnthropicProvider, GoogleProvider, LLMResult, OpenAIProvider, ToolCall, ToolSpec
from app.llm.service import LLMService
from app.models import Conversation, Document, Message, ProfileFact, ToolCallLog
from app.privacy.guard import PrivacyGuard
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.services import tools as tools_svc
from app.services.ingest import handle_message
from app.services.settings_service import DEFAULTS, set_setting
from tests.conftest import mk_msg, seed_llm

EMB = (HashingEmbedder(1024), None)
COURSE = "The meditation course runs for four days. Registration for the meditation course is open at every branch."


class ToolFake:
    """Provider factory: each generate() pops the next scripted reply (text, [ToolCall...]); records offered tools."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, name, key, base, timeout, retries):
        outer = self

        class P:
            async def generate(self, req, *, model, max_tokens, temperature, tools=None):
                outer.calls.append((req, tools))
                text, calls = outer.script.pop(0) if outer.script else ("final answer", [])
                return LLMResult(text=text, tokens_in=100, tokens_out=10, provider=name, model=model, tool_calls=list(calls))
        return P()


@pytest.fixture
async def env(session, maker, tmp_path):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None), "answer_large": ("anthropic", "small", None, None)})
    for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0),
                 ("tools_enabled", {"create_lead": True, "request_callback": True})):
        await set_setting(session, k, v)
    st = LocalStorage(str(tmp_path))
    d = Document(title="Course", file_path=st.save("c.txt", COURSE.encode()), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=EMB)

    def make(script):
        fake = ToolFake(script)
        return fake, LLMService(maker, fake)
    return make


async def send(session, llm, text, mid, uid="test:tools"):
    return await handle_message(session, mk_msg(uid=uid, text=text, mid=mid), llm=llm)


async def bot(session):
    return [m.content_redacted for m in (await session.execute(select(Message).where(Message.role == "assistant").order_by(Message.id))).scalars()]


async def logs(session):
    return [(t.tool, t.status) for t in (await session.execute(select(ToolCallLog).order_by(ToolCallLog.id))).scalars()]


LEAD = ToolCall("c1", "create_lead", {"interest": "meditation course", "phone": "[PHONE_1]"})


CALLBACK = ToolCall("c2", "request_callback", {"reason": "meditation course", "phone": "[PHONE_1]"})


async def test_an_interested_guest_goes_on_the_lead_list_at_once(session, env):
    from app.models import Lead
    fake, llm = env([("", [LEAD]), ("ধন্যবাদ! আমাদের টিম যোগাযোগ করবে।", [])])
    await send(session, llm, "ami meditation course e registration korte chai, number 01712345678", "a1")
    assert [t.name for t in fake.calls[0][1]] == ["search_knowledge", "create_lead", "request_callback", "current_datetime"]  # offered
    assert (await bot(session))[-1] == "ধন্যবাদ! আমাদের টিম যোগাযোগ করবে।"               # no yes/no question first
    assert await logs(session) == [("create_lead", "executed")]
    lead = (await session.execute(select(Lead))).scalar_one()
    assert lead.interest == "meditation course" and lead.status == "off"                # no main app in this test
    facts = {f.key: f.value for f in (await session.execute(select(ProfileFact))).scalars()}
    assert facts["courses_interested"] == "meditation course"
    assert "pending_phone" in facts and "01712345678" not in facts["pending_phone"]          # no consent: held encrypted
    notes = [m.content_redacted for m in (await session.execute(select(Message).where(Message.role == "system"))).scalars()]
    assert notes and notes[0].startswith(f"New lead #{lead.id}: meditation course")
    told = json.dumps(fake.calls[1][0].messages, ensure_ascii=False)                     # what the model was told
    assert "Saved on the lead list" in told


async def test_a_lead_without_a_phone_asks_for_one_once(session, env):
    nophone = ToolCall("c3", "create_lead", {"interest": "yoga orientation"})
    fake, llm = env([("", [nophone]), ("ফোন নম্বর দেবেন?", []), ("", [nophone]), ("ঠিক আছে", [])])
    await send(session, llm, "yoga orientation e jete chai", "n1")
    await send(session, llm, "yoga orientation e jete chai, ar ki lagbe?", "n2")
    first, second = (json.dumps(fake.calls[i][0].messages, ensure_ascii=False) for i in (1, 3))
    assert "ask ONCE" in first and "ask ONCE" not in second


async def test_callback_asks_first_and_no_or_an_unrelated_message_runs_nothing(session, env):
    fake, llm = env([("", [CALLBACK]), ("", [CALLBACK]), ("course info", [])])
    await send(session, llm, "meditation course niye amake call korben? 01712345678", "b1")
    assert "meditation course" in (await bot(session))[-1]                               # the confirmation question
    await send(session, llm, "না", "b2")
    assert (await bot(session))[-1] == DEFAULTS["tool_declined_ack"]
    await send(session, llm, "meditation course niye call korben please 01712345678", "b3")
    await send(session, llm, "how long is the meditation course?", "b4")                     # not a yes/no: just a question
    assert [s for _, s in await logs(session)] == ["declined", "expired"]


async def test_arguments_with_someone_elses_contact_are_rejected(session, env):
    fake, llm = env([("", [ToolCall("x", "create_lead", {"interest": "meditation course", "phone": "01999999999"})]), ("ok", [])])
    r = await send(session, llm, "meditation course registration korbo", "c1")
    assert await logs(session) == [("create_lead", "rejected")]
    assert (await session.get(Conversation, r.conversation.id)).pending_tool is None


async def test_disabled_tools_are_not_offered(session, env):
    await set_setting(session, "tools_enabled", {"create_lead": False, "request_callback": False, "current_datetime": False,
                                                  "search_knowledge": False})
    await session.commit()
    fake, llm = env([("plain answer", [])])
    await send(session, llm, "meditation course registration?", "d1")
    assert fake.calls[0][1] is None and await logs(session) == []


async def test_current_datetime_runs_inline_and_is_on_by_default(session, env, monkeypatch):
    await set_setting(session, "tools_enabled", {"create_lead": True})  # saved before the tool existed: default applies
    await session.commit()
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, 9, 5, tzinfo=tz)
    monkeypatch.setattr(tools_svc, "datetime", FrozenClock)
    fake, llm = env([("", [ToolCall("t", "current_datetime", {})]), ("Ekhon 3:05 PM", [])])
    await send(session, llm, "meditation course kobe? ekhon koyta baje?", "t1")
    assert [t.name for t in fake.calls[0][1]] == ["search_knowledge", "create_lead", "current_datetime"]
    assert await logs(session) == [("current_datetime", "executed")]                         # no confirmation asked
    fed = [m["content"] for m in fake.calls[1][0].messages if m["role"] == "tool"]
    assert fed and "Sunday, 18 October 2026, 03:05 PM (24h 15:05)" in fed[0]                          # 09:05 UTC = 15:05 Dhaka
    assert (await bot(session))[-1] == "Ekhon 3:05 PM"


async def test_inline_tools_get_at_most_two_rounds(session, env, monkeypatch):
    ran = []

    async def handler(ctx, args):
        ran.append(args)
        return "noted"
    inline = dataclasses.replace(tools_svc.TOOLS["create_lead"], requires_confirmation=False, handler=handler, phone=None)
    monkeypatch.setitem(tools_svc.TOOLS, "create_lead", inline)
    call = ToolCall("i", "create_lead", {"interest": "meditation course"})
    fake, llm = env([("", [call]), ("", [call]), ("", [call]), ("never used", [])])
    await send(session, llm, "meditation course registration korbo", "e1")
    assert len(ran) == 2 and [s for _, s in await logs(session)] == ["executed", "executed"]
    second_req = fake.calls[1][0]
    assert any(m["role"] == "tool" and m["content"] == "noted" for m in second_req.messages)  # result fed back, guarded


SPEC = [ToolSpec("create_lead", "save", {"type": "object", "properties": {"interest": {"type": "string"}}})]


def _round_two_request():
    req = PrivacyGuard().prepare_task("system", "call me at 01712345678")
    return PrivacyGuard.with_tool_results(req, [ToolCall("t1", "create_lead", {"interest": "yoga"})], ["saved for 01712345678"])


@respx.mock
async def test_provider_wire_formats_for_tools():
    req = _round_two_request()
    assert "01712345678" not in json.dumps(req.messages)  # tool results are redacted with the same redactor

    a = respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(200, json={
        "content": [{"type": "tool_use", "id": "tu1", "name": "create_lead", "input": {"interest": "yoga"}}], "usage": {}}))
    res = await AnthropicProvider("k").generate(req, model="m", tools=SPEC)
    body = json.loads(a.calls[0].request.content)
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert body["messages"][-2]["content"][0]["type"] == "tool_use" and body["messages"][-1]["content"][0]["type"] == "tool_result"
    assert res.tool_calls[0].name == "create_lead" and res.tool_calls[0].arguments == {"interest": "yoga"}

    o = respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "oc1", "type": "function", "function": {
            "name": "create_lead", "arguments": "{\"interest\": \"yoga\"}"}}]}}], "usage": {}}))
    res = await OpenAIProvider("k").generate(req, model="m", tools=SPEC)
    body = json.loads(o.calls[0].request.content)
    assert body["tools"][0]["type"] == "function" and body["messages"][-1]["role"] == "tool"
    assert res.tool_calls[0].id == "oc1" and res.tool_calls[0].arguments == {"interest": "yoga"}

    g = respx.post("https://generativelanguage.googleapis.com/v1beta/models/m:generateContent").mock(return_value=httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "create_lead", "args": {"interest": "yoga"}}}]}}]}))
    res = await GoogleProvider("k").generate(req, model="m", tools=SPEC)
    body = json.loads(g.calls[0].request.content)
    assert "functionDeclarations" in body["tools"][0] and "functionResponse" in body["contents"][-1]["parts"][0]
    assert res.tool_calls[0].name == "create_lead"


async def test_plain_requests_are_unchanged_without_tools():
    req = PrivacyGuard().prepare_task("system", "hello")
    url, headers, body = AnthropicProvider("k")._build(req, "m", 100, 0.2)
    assert "tools" not in body and body["messages"] == req.messages


# ---- search_knowledge: the model looks again with its own words --------------------------------------------------------
async def test_the_model_can_search_the_knowledge_itself_before_answering(env, session):
    await set_setting(session, "tools_enabled", {"search_knowledge": True})
    await session.commit()
    fake, llm = env([("", [ToolCall("s1", "search_knowledge", {"query": "meditation course registration"})]),
                     ("Registration is open at every branch.", [])])
    await send(session, llm, "where do I sign up?", "k1")
    assert (await bot(session))[-1] == "Registration is open at every branch."
    second_request = json.dumps(fake.calls[1][0].messages, ensure_ascii=False)
    assert "Registration for the meditation course is open at every branch" in second_request   # the search result
    assert ("search_knowledge", "executed") in await logs(session)
    assert any(t.name == "search_knowledge" for t in fake.calls[0][1])


async def test_no_answer_after_a_search_is_the_standard_reply_not_the_marker(env, session):
    await set_setting(session, "tools_enabled", {"search_knowledge": True})
    await session.commit()
    fake, llm = env([("", [ToolCall("s1", "search_knowledge", {"query": "internship"})]), ("NO_ANSWER", [])])
    await send(session, llm, "can I do an internship with you?", "k2")
    reply = (await bot(session))[-1]
    assert "NO_ANSWER" not in reply and reply in (DEFAULTS["no_answer_reply"], DEFAULTS["no_answer_reply_en"])
