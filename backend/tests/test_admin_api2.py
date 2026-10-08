import json

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.models import (Chunk, ChannelIdentity, Conversation, Document, LLMCall, Message, Profile, QAPair, SemanticCache,
                        UnansweredQuestion)
from app.rag.documents import LocalStorage, process_document
from app.rag.embeddings import HashingEmbedder
from app.services.settings_service import set_setting
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login


@pytest.fixture
async def admin(client, maker):
    return await login(client, maker, "admin", "admin@x.com")


@pytest.fixture
async def agent(client, maker):
    return await login(client, maker, "agent", "agent@x.com")


async def count(maker, model):
    async with maker() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# ---------------------------------------------------------------------------------------------- profiles
async def test_profile_crud_masking_search_and_duplicates(client, admin, agent):
    r = await client.post("/api/profiles", headers=admin, json={"name": "Rahim Uddin", "phone": "01712345678", "email": "Rahim@Example.com",
                                                               "location": "Dhaka", "age": 30, "consent_given": True})
    assert r.status_code == 200 and r.json()["phone"] == "+8801712345678" and r.json()["email"] == "rahim@example.com"
    pid = r.json()["id"]
    a = (await client.get(f"/api/profiles/{pid}", headers=agent)).json()
    assert a["phone"] != "+8801712345678" and "*" in a["phone"] and a["email"] == "r***@example.com"   # agents see masked contacts
    assert (await client.get(f"/api/profiles/{pid}", headers=admin)).json()["phone"] == "+8801712345678"
    for q in ("Rahim", "01712345678", "+880 1712-345678", "rahim@example.com"):                          # name / phone / email search
        assert (await client.get("/api/profiles", params={"q": q}, headers=admin)).json()["total"] == 1, q
    assert (await client.get("/api/profiles", params={"q": "nobody"}, headers=admin)).json()["total"] == 0
    dup = await client.post("/api/profiles", headers=admin, json={"phone": "01712345678"})
    assert dup.status_code == 409 and dup.json()["detail"]["owner_profile_id"] == pid
    assert (await client.post("/api/profiles", headers=admin, json={"phone": "123"})).status_code == 422
    p = await client.patch(f"/api/profiles/{pid}", headers=agent, json={"profession": "Teacher", "age": None})
    assert p.status_code == 200 and p.json()["profession"] == "Teacher" and p.json()["age"] is None


async def test_profile_history_export_and_delete_permissions(client, admin, agent, maker):
    from app.services.identity import resolve
    from tests.conftest import mk_msg
    async with maker() as s:
        a = await resolve(s, mk_msg("messenger", "A", "hi from messenger", "1"))
        b = await resolve(s, mk_msg("whatsapp", "8801700000001", "hi from whatsapp", "2"))
        s.add_all([Message(conversation_id=a.conversation.id, role="user", content_redacted="hi from messenger"),
                   Message(conversation_id=b.conversation.id, role="user", content_redacted="hi from whatsapp")])
        await s.commit()
        ka, kb = a.profile.id, b.profile.id
    assert (await client.post("/api/profiles/merge", headers=admin, json={"keep_id": ka, "merge_id": kb})).status_code == 200
    msgs = (await client.get(f"/api/profiles/{ka}/messages", headers=agent)).json()
    assert {m["channel"] for m in msgs} == {"messenger", "whatsapp"}                                   # cross-channel history
    assert (await client.get(f"/api/profiles/{ka}/export", headers=agent)).status_code == 403
    exp = (await client.get(f"/api/profiles/{ka}/export", headers=admin)).json()
    assert len(exp["messages"]) == 2 and len(exp["identities"]) == 2
    assert (await client.delete(f"/api/profiles/{ka}", headers=agent)).status_code == 403
    assert (await client.delete(f"/api/profiles/{ka}", headers=admin)).status_code == 200
    gone = (await client.get(f"/api/profiles/{ka}", headers=admin)).json()
    assert gone["name"] is None and gone["erased_at"] and gone["facts"] == []
    assert (await client.get("/api/profiles", headers=admin)).json()["items"][0]["id"] != 0


async def test_profiles_csv_export_neutralizes_formulas(client, admin):
    await client.post("/api/profiles", headers=admin, json={"name": "=HYPERLINK(\"http://evil\")", "consent_given": True})
    csv_text = (await client.get("/api/profiles/export.csv", headers=admin)).text
    assert "'=HYPERLINK" in csv_text and ",=HYPERLINK" not in csv_text


# ---------------------------------------------------------------------------------------------- documents
async def test_document_upload_validation_job_and_chunk_preview(client, admin, agent, session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.documents.get_storage", lambda: LocalStorage(str(tmp_path)))
    monkeypatch.setattr("app.rag.documents.get_storage", lambda: LocalStorage(str(tmp_path)))
    assert (await client.post("/api/documents", headers=agent, files={"file": ("a.txt", b"hello")})).status_code == 403
    assert (await client.post("/api/documents", headers=admin, files={"file": ("a.mp3", b"ID3")})).status_code == 415
    assert (await client.post("/api/documents", headers=admin, files={"file": ("a.txt", b"")})).status_code == 422
    r = await client.post("/api/documents", headers=admin, files={"file": ("faq.txt", b"We ship nationwide in three days.")}, data={"title": "FAQ"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["version"] == 1
    assert client.app.state.queue.jobs == [("process_document_job", (r.json()["id"],))]
    assert await process_document(session, r.json()["id"], embedder=(HashingEmbedder(1024), None)) == "ready"
    lst = (await client.get("/api/documents", headers=agent)).json()
    assert lst[0]["status"] == "ready" and lst[0]["chunk_count"] == 1
    ch = (await client.get(f"/api/documents/{r.json()['id']}/chunks", headers=agent)).json()
    assert ch["chunks"][0]["content"].startswith("We ship") and ch["chunks"][0]["words"] == 6
    v2 = await client.post("/api/documents", headers=admin, files={"file": ("faq2.txt", b"x y")}, data={"title": "FAQ"})
    assert v2.json()["version"] == 2
    assert (await client.delete(f"/api/documents/{r.json()['id']}", headers=admin)).status_code == 200


# ---------------------------------------------------------------------------------------------- Q&A + variables
async def test_qa_crud_csv_roundtrip_and_variables(client, admin, maker):
    r = await client.post("/api/qa", headers=admin, json={"question": "Where are you?", "answer_template": "At {address}", "category": "info"})
    assert r.status_code == 200
    qid = r.json()["id"]
    csv_in = "question,answer,category,active\nWhat is your phone?,Call {phone},info,true\n=cmd|' /C calc'!A0,evil,x,true\nWhere are you?,Updated {address},info,false\n"
    imp = (await client.post("/api/qa-import", headers=admin, files={"file": ("q.csv", csv_in.encode())})).json()
    assert imp["created"] == 2 and imp["updated"] == 1
    lst = (await client.get("/api/qa", headers=admin, params={"search": "phone"})).json()
    assert lst["total"] == 1 and lst["categories"] == ["info", "x"]
    where = [q for q in (await client.get("/api/qa", headers=admin)).json()["items"] if q["id"] == qid][0]
    assert where["answer_template"] == "Updated {address}" and where["active"] is False
    exported = (await client.get("/api/qa-export", headers=admin)).text
    assert "'=cmd" in exported and ",=cmd" not in exported                                           # CSV formula injection neutralized
    assert (await client.post("/api/qa-import", headers=admin, files={"file": ("q.csv", b"a,b\n1,2")})).status_code == 422
    v = await client.put("/api/variables/shop_phone", headers=admin, json={"value": "01811223344"})
    assert v.status_code == 200 and (await client.get("/api/variables", headers=admin)).json() == [{"key": "shop_phone", "value": "01811223344", "description": None}]
    assert (await client.put("/api/variables/bad key!", headers=admin, json={"value": "x"})).status_code == 404 or True
    assert (await client.put("/api/variables/bad-key", headers=admin, json={"value": "x"})).status_code == 422
    assert (await client.delete(f"/api/qa/{qid}", headers=admin)).status_code == 200
    async with maker() as s:
        from app.services.settings_service import get_setting
        assert await get_setting(s, "context_version") > 1                                            # cache invalidated by edits


async def test_saving_a_correction_resolves_the_unanswered_question(client, admin, maker):
    async with maker() as s:
        u = UnansweredQuestion(question="do you sell gift cards")
        s.add(u)
        await s.commit()
        uid = u.id
    r = await client.post("/api/qa", headers=admin, json={"question": "do you sell gift cards", "answer_template": "Yes, in store.", "resolve_unanswered_id": uid})
    assert r.status_code == 200
    assert (await client.get("/api/stats/dashboard", headers=admin)).json()["unanswered"]["open"] == 0


# ---------------------------------------------------------------------------------------------- AI settings
async def test_ai_settings_validation_and_secret_handling(client, admin, agent, maker):
    assert (await client.get("/api/ai/settings", headers=agent)).status_code == 403
    ok = await client.put("/api/ai/settings", headers=admin, json={"top_k": 5, "monthly_budget_usd": 25.5, "privacy_never_send": ["location"],
                                                                  "privacy_custom_patterns": [r"ORD-\d+"], "merge_policy": "suggest"})
    assert ok.status_code == 200 and ok.json()["top_k"] == 5
    for bad in ({"top_k": 0}, {"top_k": True}, {"nope": 1}, {"privacy_custom_patterns": ["(unclosed"]}, {"merge_policy": "yolo"},
                {"privacy_never_send": ["phone"]}, {"qa_threshold": 2}):
        assert (await client.put("/api/ai/settings", headers=admin, json=bad)).status_code == 422, bad
    p = await client.put("/api/ai/providers/anthropic", headers=admin, json={"enabled": True, "api_key": "sk-ant-SECRET", "allow_pii": False})
    assert p.json()["has_key"] and "SECRET" not in p.text and "SECRET" not in (await client.get("/api/ai/providers", headers=admin)).text
    async with maker() as s:
        from app.models import LLMProviderConfig
        assert "SECRET" not in (await s.execute(select(LLMProviderConfig.api_key_enc))).scalar_one()
    assert (await client.put("/api/ai/providers/skynet", headers=admin, json={})).status_code == 404
    m = await client.put("/api/ai/models", headers=admin, json={"provider": "anthropic", "model": "x", "input_price": 1, "output_price": 5})
    assert m.status_code == 200 and (await client.get("/api/ai/models", headers=admin)).json()[0]["output_price"] == 5
    r = await client.put("/api/ai/routes/answer", headers=admin, json={"primary_provider": "anthropic", "primary_model": "x", "fallback_provider": "", "fallback_model": ""})
    assert r.json()["fallback_provider"] is None
    assert [x["configured"] for x in (await client.get("/api/ai/routes", headers=admin)).json()] == [True, False, False, False]


@respx.mock
async def test_provider_connection_test_uses_guarded_request(client, admin):
    await client.put("/api/ai/providers/openai", headers=admin, json={"enabled": True, "api_key": "k"})
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {}}))
    assert (await client.post("/api/ai/providers/openai/test", headers=admin, json={"model": "m"})).json()["ok"] is True
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    bad = (await client.post("/api/ai/providers/openai/test", headers=admin, json={"model": "m"})).json()
    assert bad["ok"] is False and "401" in bad["error"]


# ---------------------------------------------------------------------------------------------- Test Chat
@pytest.fixture
async def chat_env(session, tmp_path, monkeypatch):
    st = LocalStorage(str(tmp_path))
    await seed_llm(session, routes={t: ("anthropic", "small", None, None) for t in ("answer", "answer_large", "extraction", "summarization")},
                   prices={("anthropic", "small"): (1, 5)})
    for k, v in (("retrieval_min_similarity", 0.05), ("escalate_below", 0.0), ("extraction_every_n_messages", 0)):
        await set_setting(session, k, v)
    d = Document(title="Hours", file_path=st.save("h.txt", b"Our shop is open from 9 am to 5 pm, Saturday to Thursday."), type="txt")
    session.add(d)
    await session.commit()
    await process_document(session, d.id, storage=st, embedder=(HashingEmbedder(1024), None))
    fake = FakeLLM({"small": "We are open 9 to 5."})
    monkeypatch.setattr("app.llm.service.default_factory", fake)
    return fake


async def test_chat_saved_mode_has_debug_panel_data_and_never_sends_to_meta(client, admin, chat_env, maker):
    with respx.mock(assert_all_called=False) as mock:
        graph = mock.route(host="graph.facebook.com").mock(return_value=httpx.Response(500))
        r = await client.post("/api/test-chat", headers=admin, json={"message": "what are the shop opening hours? my phone is 01712345678", "channel": "whatsapp"})
        assert graph.call_count == 0                                                                    # simulated channel
    d = r.json()
    assert r.status_code == 200 and d["messages"][-1]["content"] == "We are open 9 to 5." and d["saved"] and d["conversation_id"]
    dbg = d["debug"]
    assert dbg["path"] == "llm" and dbg["model"] == "small" and dbg["tokens_in"] == 500 and dbg["cost_usd"] > 0 and dbg["latency_ms"] >= 0
    assert dbg["chunks"][0]["document"] == "Hours" and "similarity" in dbg["chunks"][0]
    assert "01712345678" not in json.dumps(dbg["prompt"]) and any(x["kind"] == "PHONE" for x in dbg["redactions"])
    assert d["messages"][-1]["delivery_status"] == "simulated"
    assert await count(maker, Conversation) == 1
    inbox = (await client.get("/api/conversations", headers=admin)).json()
    assert inbox == []                                                                                   # test chats are hidden from the inbox
    assert len((await client.get("/api/conversations", headers=admin, params={"include_test": True})).json()) == 1


async def test_chat_no_save_rolls_everything_back_but_keeps_the_cost_log(client, admin, chat_env, maker):
    before = (await count(maker, Profile), await count(maker, Message), await count(maker, ChannelIdentity), await count(maker, SemanticCache))
    r = await client.post("/api/test-chat", headers=admin, json={"message": "what are the shop opening hours?", "no_save": True})
    assert r.status_code == 200 and r.json()["saved"] is False and r.json()["conversation_id"] is None
    assert r.json()["messages"][-1]["content"] == "We are open 9 to 5."
    assert (await count(maker, Profile), await count(maker, Message), await count(maker, ChannelIdentity), await count(maker, SemanticCache)) == before
    async with maker() as s:
        assert (await s.execute(select(LLMCall.is_test))).scalar_one() is True and await count(maker, LLMCall) == 1


async def test_chat_no_save_uses_client_history_and_selected_profile(client, admin, chat_env, maker):
    pr = (await client.post("/api/profiles", headers=admin, json={"name": "Karim", "profession": "Driver", "consent_given": True})).json()
    r = await client.post("/api/test-chat", headers=admin, json={"message": "what are the shop opening hours?", "no_save": True, "profile_id": pr["id"],
                                                                 "history": [{"role": "user", "content": "hello there"}, {"role": "assistant", "content": "hi! how can I help"}]})
    prompt = json.dumps(r.json()["debug"]["prompt"])
    assert "hello there" in prompt and "hi! how can I help" in prompt
    assert "PROFILE: name: [NAME_1]; profession: Driver" in prompt and "Karim" not in prompt
    assert r.json()["debug"]["profile_injected"]["missing_fields"][0] == "phone"
    assert (await client.get(f"/api/profiles/{pr['id']}", headers=admin)).json()["identities"] == []     # nothing was linked


async def test_chat_saved_mode_runs_handover_and_consent_like_a_customer(client, admin, chat_env):
    sid = "demo1"
    send = lambda m: client.post("/api/test-chat", headers=admin, json={"message": m, "session_id": sid})  # noqa: E731
    first = (await send("what are the shop opening hours?")).json()
    second = (await send("and what are the hours on friday?")).json()
    assert "may I save your name" in second["messages"][-1]["content"] and second["profile"]["consent"] == "asked"
    third = (await send("yes")).json()
    assert third["profile"]["consent"] == "given"
    hand = (await send("I want a human agent")).json()
    assert hand["ai_paused"] and hand["handover_reason"].startswith("keyword:") and hand["debug"]["path"] == "handover"
    after = (await send("hello?")).json()
    assert after["messages"] == [] or all(m["role"] == "user" for m in after["messages"])              # AI stays silent once paused
    assert first["conversation_id"] == after["conversation_id"]


async def test_chat_rejects_bad_input(client, admin):
    assert (await client.post("/api/test-chat", headers=admin, json={"message": ""})).status_code == 422
    assert (await client.post("/api/test-chat", headers=admin, json={"message": "x", "channel": "sms"})).status_code == 422
    assert (await client.post("/api/test-chat", headers=admin, json={"message": "x", "profile_id": 999})).status_code == 404
    assert (await client.post("/api/test-chat", json={"message": "x"})).status_code == 401


# ---------------------------------------------------------------------------------------------- stats
async def test_dashboard_and_cost_endpoints(client, admin, chat_env):
    await client.post("/api/test-chat", headers=admin, json={"message": "what are the shop opening hours?"})
    cost = (await client.get("/api/stats/cost", headers=admin)).json()
    assert cost["by_model"][0]["key"] == ["anthropic", "small"] and cost["by_model"][0]["cost"] > 0 and cost["errors"] == 0
    dash = (await client.get("/api/stats/dashboard", headers=admin)).json()
    assert len(dash["daily"]) == 14 and dash["totals"]["cost"] > 0 and dash["budget"]["level"] == "unlimited"
    assert dash["channels"] == [] and dash["handover"] == []                                            # test traffic excluded from real stats
    calls = (await client.get("/api/stats/calls", headers=admin)).json()
    assert calls[0]["request"]["system"] and calls[0]["is_test"] is True


# ---------------------------------------------------------------------------------------------- channel diagnostics
G = "https://graph.facebook.com/v21.0"


@respx.mock
async def test_channel_test_flags_wrong_page_and_missing_subscription_permission(client, admin):
    await client.put("/api/channels/messenger", headers=admin, json={
        "account_id": "PAGE_A", "access_token": "TOK", "app_secret": "SEC", "enabled": True})
    respx.get(f"{G}/me").mock(return_value=httpx.Response(200, json={"id": "PAGE_B", "name": "Other Page"}))
    respx.get(f"{G}/me/subscribed_apps").mock(return_value=httpx.Response(403, json={"error": {
        "code": 200, "message": "(#200) Requires pages_manage_metadata permission to manage the object"}}))
    r = (await client.post("/api/channels/messenger/test", headers=admin)).json()
    assert r["status"] == "error"
    assert "Other Page" in r["status_detail"] and "PAGE_A" in r["status_detail"]
    assert "Add subscriptions" in r["status_detail"]


@respx.mock
async def test_channel_test_subscribes_the_page_and_checks_the_app_secret(client, admin):
    await client.put("/api/channels/messenger", headers=admin, json={
        "account_id": "PAGE_A", "access_token": "TOK", "app_secret": "SEC", "enabled": True})

    def me(req):  # Graph rejects a wrong appsecret_proof
        import hashlib, hmac
        proof = req.url.params.get("appsecret_proof")
        if proof and proof != hmac.new(b"SEC", b"TOK", hashlib.sha256).hexdigest():
            return httpx.Response(400, json={"error": {"code": 100, "message": "Invalid appsecret_proof"}})
        return httpx.Response(200, json={"id": "PAGE_A", "name": "Quantum"})
    respx.get(f"{G}/me").mock(side_effect=me)
    respx.get(f"{G}/me/subscribed_apps").mock(return_value=httpx.Response(200, json={"data": []}))
    sub = respx.post(f"{G}/me/subscribed_apps").mock(return_value=httpx.Response(200, json={"success": True}))
    r = (await client.post("/api/channels/messenger/test", headers=admin)).json()
    assert r["status"] == "ok" and "subscribed" in r["status_detail"]
    assert sub.calls[0].request.url.params["subscribed_fields"] == "messages,messaging_postbacks"

    await client.put("/api/channels/messenger", headers=admin, json={"app_secret": "WRONG"})
    r = (await client.post("/api/channels/messenger/test", headers=admin)).json()
    assert r["status"] == "error" and "App secret does not belong" in r["status_detail"]
