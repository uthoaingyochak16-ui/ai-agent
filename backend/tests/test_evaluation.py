import io

from sqlalchemy import select

from app.llm.service import LLMService
from app.models import EvalCase, EvalResult, EvalRun, LLMCall, QAPair
from app.privacy.guard import GuardedRequest
from app.rag.templates import normalize_question
from app.services.evaluation import run_eval, settings_snapshot
from tests.conftest import seed_llm
from tests.helpers import FakeLLM


async def test_eval_run_uses_fake_judge_and_cheapest_configured_model(session, maker):
    await seed_llm(session, providers=("anthropic", "openai"), routes={
        "answer": ("anthropic", "expensive", "openai", "cheap"),
    }, prices={("anthropic", "expensive"): (10, 30), ("openai", "cheap"): (0.1, 0.2)})
    question, expected = "Quantum course fee koto?", "The course fee is 11,500 taka."
    session.add(QAPair(question=question, question_norm=normalize_question(question), answer_template=expected,
                       embedding=[0.0] * 1024, active=True))
    case = EvalCase(question=question, expected_answer=expected, language="banglish", tags=["fees"], active=True)
    session.add(case)
    await session.flush()
    run = EvalRun(status="queued", settings_snapshot=await settings_snapshot(session), total=1, completed=0)
    session.add(run)
    await session.commit()

    fake = FakeLLM({"cheap": '{"correct": true, "grounded": true, "reason": "Exact Q&A match"}'})
    result = await run_eval(maker, run.id, llm=LLMService(maker, fake))
    assert result["status"] == "done"
    async with maker() as check:
        stored = (await check.execute(select(EvalResult))).scalar_one()
        finished = await check.get(EvalRun, run.id)
        assert finished.status == "done" and finished.completed == 1
        assert stored.actual_answer == expected and stored.path == "qa_shortcut"
        assert stored.correct and stored.grounded and not stored.missed
        assert stored.tokens_in == 500 and stored.tokens_out == 50
    assert fake.models() == ["cheap"]
    assert isinstance(fake.calls[0][1], GuardedRequest)


async def test_eval_no_context_is_marked_missed_without_calling_judge(session, maker):
    await seed_llm(session, routes={"answer": ("anthropic", "small", None, None)},
                   prices={("anthropic", "small"): (1, 2)})
    case = EvalCase(question="A fact absent from all knowledge", expected_answer="A known expected answer",
                    language="en", active=True)
    session.add(case)
    await session.flush()
    run = EvalRun(status="queued", settings_snapshot={}, total=1, completed=0)
    session.add(run)
    await session.commit()
    fake = FakeLLM({"small": '{"correct": true, "grounded": true, "reason": "wrong"}'})

    await run_eval(maker, run.id, llm=LLMService(maker, fake))
    async with maker() as check:
        stored = (await check.execute(select(EvalResult))).scalar_one()
        assert stored.path == "no_context" and stored.missed and not stored.correct
        assert "no_context" in stored.reason
    assert fake.calls == []


async def test_eval_follow_up_case_uses_saved_history_and_rewrite_route(session, maker):
    await seed_llm(session, routes={
        "answer": ("anthropic", "judge", None, None),
        "rewrite": ("anthropic", "rewrite", None, None),
    }, prices={("anthropic", "judge"): (0.1, 0.1), ("anthropic", "rewrite"): (1, 1)})
    expected = "The Quantum Method course fee is 11,500 taka."
    session.add(QAPair(question="Quantum Method course fee koto?", question_norm=normalize_question("Quantum Method course fee koto?"),
                       answer_template=expected, embedding=[0.0] * 1024, active=True))
    case = EvalCase(question="Oita koto?", expected_answer=expected, language="banglish", tags=["follow_up"],
                    history=[{"role": "user", "content": "Quantum Method course fee koto?"},
                             {"role": "assistant", "content": "I can help with the course."}])
    session.add(case)
    await session.flush()
    run = EvalRun(status="queued", settings_snapshot={}, total=1, completed=0)
    session.add(run)
    await session.commit()
    fake = FakeLLM({"rewrite": "Quantum Method course fee koto?",
                    "judge": '{"correct": true, "grounded": true, "reason": "Correct"}'})

    await run_eval(maker, run.id, llm=LLMService(maker, fake))
    async with maker() as check:
        stored = (await check.execute(select(EvalResult))).scalar_one()
        tasks = (await check.execute(select(LLMCall.task).order_by(LLMCall.id))).scalars().all()
        assert stored.path == "qa_shortcut" and stored.correct
        assert tasks == ["rewrite", "evaluation_judge"]
    assert fake.models() == ["rewrite", "judge"]


async def test_eval_case_crud_and_csv_import(client, maker):
    from app.core.security import hash_password
    from app.models import AdminUser
    async with maker() as session:
        session.add(AdminUser(email="eval@example.com", name="Eval", role="admin",
                              password_hash=hash_password("correct-horse-battery")))
        await session.commit()
    login = await client.post("/api/auth/login", json={"email": "eval@example.com", "password": "correct-horse-battery"})
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    made = await client.post("/api/eval/cases", headers=headers, json={
        "question": "Next program date kobe?", "expected_answer": "October 20", "language": "banglish",
        "tags": ["program"], "history": [{"role": "user", "content": "Tell me about the next program"}], "active": True,
    })
    assert made.status_code == 200 and made.json()["tags"] == ["program"] and made.json()["history"][0]["role"] == "user"
    csv_data = b"question,expected_answer,language\nHello,Hi there,en\n"
    imported = await client.post("/api/eval/import", headers=headers,
                                 files={"file": ("eval.csv", io.BytesIO(csv_data), "text/csv")})
    assert imported.status_code == 200 and imported.json()["created"] == 1
    rows = (await client.get("/api/eval/cases", headers=headers)).json()
    assert len(rows) == 2
    deleted = await client.delete(f"/api/eval/cases/{made.json()['id']}", headers=headers)
    assert deleted.status_code == 200
