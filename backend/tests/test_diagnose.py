"""Train the assistant -> Find the reason: facts from the reply trace, the AI's explanation and fixes."""
import json

import pytest

import app.llm.service as svc
from app.services.diagnose import signals
from tests.conftest import seed_llm
from tests.helpers import FakeLLM
from tests.test_admin_api import login


def test_signals_say_where_the_reply_came_from():
    assert "ready-made text" in signals({"path": "greeting"})[0]
    assert "saved Q&A answered it" in signals({"path": "qa_shortcut", "qa_match": {"id": 7, "question": "fee?", "similarity": 1}})[0]
    assert "found nothing related" in signals({"path": "no_context", "chunks": []})[0]
    assert "judged none of them answers" in signals({"path": "no_context", "chunks": [{"document": "FAQ"}]})[0]
    assert "Payment questions" in signals({"path": "payment_disabled"})[0]
    blocked = signals({"path": "llm", "output_blocked": True, "output_reasons": ["foreign_contact_data"]})
    assert any("safety filter blocked" in x for x in blocked)
    down = signals({"path": "llm", "steps": ["embedding unavailable (429): word search only"]})
    assert any("meaning search was down" in x for x in down)


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(svc, "default_factory", f)
    return f


async def test_find_the_reason_runs_the_question_again_and_returns_fixes(client, maker, session, fake):
    await seed_llm(session, routes={"answer": ("anthropic", "big", None, None)})
    fake.script["big"] = json.dumps({
        "summary": "নলেজ বেসে সিলেট অফিসের তথ্য নেই।", "cause": "missing_knowledge",
        "fixes": [{"type": "knowledge", "note": "Add the Sylhet office address"},
                  {"type": "qa", "question": "Sylhet office kothay?", "answer": "..."},
                  {"type": "bogus", "text": "x"}]})
    admin = await login(client, maker)
    r = (await client.post("/api/teach/diagnose", json={"question": "Sylhet office kothay?",
                                                        "complaint": "eta keno uttor dite parlo na?"}, headers=admin)).json()
    assert r["ran_again"] and r["path"] == "no_context" and r["cause"] == "missing_knowledge"
    assert any("found nothing related" in f for f in r["facts"])
    assert [f["type"] for f in r["fixes"]] == ["knowledge", "qa"]
    prompt = fake.calls[-1][1].messages[0]["content"]
    assert "eta keno uttor dite parlo na?" in prompt and "found nothing related" in prompt


async def test_the_teaching_chat_diagnoses_when_staff_ask_why(client, maker, session, fake):
    await seed_llm(session, routes={"answer": ("anthropic", "big", None, None)})
    answers = iter([
        json.dumps({"reply": "দেখছি।", "instructions": [], "replaces": [], "diagnose": "Sylhet office kothay?"}),
        json.dumps({"summary": "তথ্য নেই।", "cause": "missing_knowledge",
                    "fixes": [{"type": "rule", "text": "When an office is not in the knowledge, give the head office number."}]}),
    ])
    fake.script["big"] = lambda req: next(answers)
    admin = await login(client, maker)
    r = (await client.post("/api/teach/chat", json={"messages": [
        {"role": "staff", "content": "Sylhet office kothay jiggesh korle keno parlo na?"}]}, headers=admin)).json()
    assert r["reply"] == "তথ্য নেই।" and r["diagnosis"]["question"] == "Sylhet office kothay?"
    assert r["instructions"] == ["When an office is not in the knowledge, give the head office number."]
