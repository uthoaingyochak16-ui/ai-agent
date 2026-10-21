from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import any_staff
from app.db.session import get_session
from app.models import EvalCase, EvalResult, EvalRun
from app.services.evaluation import settings_snapshot

router = APIRouter(prefix="/api/eval", tags=["evaluation"], dependencies=[Depends(any_staff)])


class CaseIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    expected_answer: str = Field(min_length=1, max_length=12000)
    expected_path: str | None = Field(None, max_length=32)
    language: str = Field("unknown", min_length=1, max_length=24)
    tags: list[str] = Field(default_factory=list, max_length=30)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    active: bool = True


def _case(row: EvalCase) -> dict:
    return {"id": row.id, "question": row.question, "expected_answer": row.expected_answer,
            "expected_path": row.expected_path, "language": row.language, "tags": row.tags, "history": row.history,
            "active": row.active, "created_at": row.created_at}


def _history(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": item["role"], "content": item["content"][:4000]} for item in items
            if item.get("role") in ("user", "assistant") and item.get("content")]


@router.get("/cases")
async def list_cases(active: bool | None = None, session: AsyncSession = Depends(get_session)):
    query = select(EvalCase)
    if active is not None:
        query = query.where(EvalCase.active.is_(active))
    rows = (await session.execute(query.order_by(EvalCase.id.desc()))).scalars().all()
    return [_case(row) for row in rows]


@router.post("/cases")
async def create_case(body: CaseIn, session: AsyncSession = Depends(get_session)):
    row = EvalCase(question=body.question.strip(), expected_answer=body.expected_answer.strip(),
                   expected_path=body.expected_path or None, language=body.language.strip().lower(),
                   tags=sorted(set(tag.strip()[:50] for tag in body.tags if tag.strip())),
                   history=_history(body.history), active=body.active)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _case(row)


STARTER = Path(__file__).resolve().parents[2] / "scripts" / "golden_set.json"


@router.post("/cases/starter")
async def add_starter_cases(session: AsyncSession = Depends(get_session)):
    """Ready-made tests (scripts/golden_set.json): the first guest message of each checked chat and what its reply must
    do. Questions already in the test set are skipped, so pressing it twice adds nothing."""
    try:
        cases = json.loads(STARTER.read_text(encoding="utf-8")).get("cases", [])
    except (OSError, ValueError):
        raise HTTPException(404, "The ready-made test set is not available on this server") from None
    have = set((await session.execute(select(EvalCase.question))).scalars())
    added = 0
    for c in cases:
        turn = (c.get("turns") or [{}])[0]
        q, expect = (turn.get("user") or "").strip(), (turn.get("expect") or "").strip()
        if not q or not expect or q in have:
            continue
        lang = "bn" if any("ঀ" <= ch <= "৿" for ch in q) else "en" if "english" in (c.get("tags") or []) else "banglish"
        session.add(EvalCase(question=q[:4000], expected_answer=expect[:12000], language=lang,
                             tags=sorted({"ready-made", *[str(t)[:50] for t in c.get("tags") or []]}), history=[], active=True))
        have.add(q)
        added += 1
    await session.commit()
    return {"added": added, "available": len(cases)}


@router.put("/cases/{case_id}")
async def update_case(case_id: int, body: CaseIn, session: AsyncSession = Depends(get_session)):
    row = await session.get(EvalCase, case_id)
    if not row:
        raise HTTPException(404, "Evaluation case not found")
    row.question, row.expected_answer = body.question.strip(), body.expected_answer.strip()
    row.expected_path, row.language, row.active = body.expected_path or None, body.language.strip().lower(), body.active
    row.tags = sorted(set(tag.strip()[:50] for tag in body.tags if tag.strip()))
    row.history = _history(body.history)
    await session.commit()
    await session.refresh(row)
    return _case(row)


@router.delete("/cases/{case_id}")
async def delete_case(case_id: int, session: AsyncSession = Depends(get_session)):
    row = await session.get(EvalCase, case_id)
    if not row:
        raise HTTPException(404, "Evaluation case not found")
    await session.delete(row)
    await session.commit()
    return {"ok": True}


@router.post("/import")
async def import_cases(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    data = await file.read(5 * 1024 * 1024)
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(422, "Could not read CSV") from exc
    normalized = [{str(k).strip().lower(): str(v or "").strip() for k, v in row.items()} for row in rows]
    if rows and not {"question", "expected_answer", "language"}.issubset(normalized[0]):
        raise HTTPException(422, "CSV needs question, expected_answer, and language columns")
    created = skipped = 0
    for row in normalized[:5000]:
        if not row.get("question") or not row.get("expected_answer") or not row.get("language"):
            skipped += 1
            continue
        tags = [tag.strip() for tag in row.get("tags", "").split(",") if tag.strip()]
        try:
            history = json.loads(row.get("history", "[]"))
        except json.JSONDecodeError:
            history = []
        session.add(EvalCase(question=row["question"][:4000], expected_answer=row["expected_answer"][:12000],
                             expected_path=(row.get("expected_path") or None), language=row["language"][:24].lower(),
                             tags=tags[:30], history=_history(history) if isinstance(history, list) else [],
                             active=row.get("active", "true").lower() not in ("false", "0", "no")))
        created += 1
    await session.commit()
    return {"created": created, "skipped": skipped}


def _metrics(results: list[tuple[EvalResult, EvalCase]]) -> dict:
    total = len(results)
    by_language: dict[str, dict] = {}
    for result, case in results:
        item = by_language.setdefault(case.language, {"total": 0, "correct": 0, "missed": 0, "not_grounded": 0})
        item["total"] += 1
        item["correct"] += int(result.correct)
        item["missed"] += int(result.missed)
        item["not_grounded"] += int(not result.grounded)
    for item in by_language.values():
        n = item["total"] or 1
        item.update(accuracy=item["correct"] / n, missed_rate=item["missed"] / n,
                    hallucination_rate=item["not_grounded"] / n)
    cost = sum((result.cost_usd or Decimal(0)) + (result.judge_cost_usd or Decimal(0)) for result, _ in results)
    return {"total": total, "accuracy": sum(r.correct for r, _ in results) / total if total else 0,
            "missed_rate": sum(r.missed for r, _ in results) / total if total else 0,
            "hallucination_rate": sum(not r.grounded for r, _ in results) / total if total else 0,
            "cost_usd": float(cost), "by_language": by_language}


async def _result_rows(session: AsyncSession, run_id: int) -> list[tuple[EvalResult, EvalCase]]:
    return list((await session.execute(select(EvalResult, EvalCase).join(EvalCase, EvalCase.id == EvalResult.case_id)
                                       .where(EvalResult.run_id == run_id).order_by(EvalResult.case_id))).all())


@router.post("/runs")
async def create_run(request: Request, session: AsyncSession = Depends(get_session)):
    total = (await session.execute(select(func.count()).select_from(EvalCase).where(EvalCase.active.is_(True)))).scalar_one()
    if not total:
        raise HTTPException(422, "Add at least one active evaluation case first")
    run = EvalRun(status="queued", settings_snapshot=await settings_snapshot(session), total=total, completed=0)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    job = await request.app.state.queue.enqueue_job("eval_run_job", run.id, _job_id=f"eval-{run.id}")
    if not job:
        run.status, run.error = "failed", "Could not enqueue evaluation job"
        await session.commit()
        raise HTTPException(503, run.error)
    return {"id": run.id, "status": run.status, "total": run.total, "completed": run.completed}


@router.get("/runs")
async def list_runs(session: AsyncSession = Depends(get_session)):
    runs = (await session.execute(select(EvalRun).order_by(EvalRun.id.desc()).limit(100))).scalars().all()
    payload = []
    for run in runs:
        results = await _result_rows(session, run.id)
        payload.append({"id": run.id, "status": run.status, "total": run.total, "completed": run.completed,
                        "error": run.error, "created_at": run.created_at, "finished_at": run.finished_at,
                        "metrics": _metrics(results)})
    return payload


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Evaluation run not found")
    rows = await _result_rows(session, run_id)
    previous = (await session.execute(select(EvalRun).where(EvalRun.id < run_id, EvalRun.status == "done")
                                      .order_by(EvalRun.id.desc()).limit(1))).scalar_one_or_none()
    previous_rows = await _result_rows(session, previous.id) if previous else []
    previous_by_case = {result.case_id: result for result, _ in previous_rows}
    current_metrics, previous_metrics = _metrics(rows), _metrics(previous_rows) if previous else None
    results = []
    for result, case in rows:
        old = previous_by_case.get(case.id)
        results.append({"id": result.id, "case_id": case.id, "question": case.question,
                        "expected_answer": case.expected_answer, "expected_path": case.expected_path,
                        "language": case.language, "tags": case.tags, "actual_answer": result.actual_answer,
                        "path": result.path, "latency_ms": result.latency_ms, "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out, "cost_usd": float(result.cost_usd + result.judge_cost_usd),
                        "retrieved_chunk_ids": result.retrieved_chunk_ids, "correct": result.correct,
                        "grounded": result.grounded, "missed": result.missed, "reason": result.reason,
                        "previous": None if not old else {"correct": old.correct, "grounded": old.grounded,
                                                             "missed": old.missed, "path": old.path,
                                                             "actual_answer": old.actual_answer}})
    delta = None
    if previous_metrics:
        delta = {key: current_metrics[key] - previous_metrics[key]
                 for key in ("accuracy", "missed_rate", "hallucination_rate", "cost_usd")}
    return {"id": run.id, "status": run.status, "total": run.total, "completed": run.completed,
            "error": run.error, "created_at": run.created_at, "finished_at": run.finished_at,
            "settings_snapshot": run.settings_snapshot, "metrics": current_metrics,
            "previous_run_id": previous.id if previous else None, "delta": delta, "results": results}
