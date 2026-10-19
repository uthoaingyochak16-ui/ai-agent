import asyncio

from sqlalchemy import select, text

from app.models import Message, WebhookEvent
from app.services import ingest
from app.services.ingest import process_event
from app.services.reply_engine import ReplyResult
from app.services.settings_service import set_setting
from tests.conftest import mk_msg


class Queue:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, args, kwargs))
        return object()


async def add_event(maker, uid: str, mid: str, text: str) -> int:
    async with maker() as session:
        ev = WebhookEvent(channel="messenger", external_id=mid, sender_id=uid,
                          payload=mk_msg(uid=uid, mid=mid, text=text).to_json())
        session.add(ev)
        await session.commit()
        return ev.id


async def debounce_off(maker) -> None:
    async with maker() as session:
        await set_setting(session, "debounce_seconds", 0)
        await session.commit()


async def test_same_guest_events_are_strictly_sequential_and_second_sees_first_reply(maker, monkeypatch):
    await debounce_off(maker)
    first = await add_event(maker, "test:ordered", "ord-1", "first detailed question")
    second = await add_event(maker, "test:ordered", "ord-2", "second detailed question")
    entered = asyncio.Event()
    release = asyncio.Event()
    seen_assistant_counts = []

    async def fake_reply(session, r, question, **kw):
        count = len((await session.execute(select(Message).where(
            Message.conversation_id == r.conversation.id, Message.role == "assistant"))).scalars().all())
        seen_assistant_counts.append(count)
        if len(seen_assistant_counts) == 1:
            entered.set()
            await release.wait()
        return ReplyResult(f"answer {question}", "llm", debug={})

    monkeypatch.setattr(ingest, "generate_reply", fake_reply)
    one = asyncio.create_task(process_event(maker, first))
    await entered.wait()
    two = asyncio.create_task(process_event(maker, second))
    await asyncio.sleep(0.2)
    assert seen_assistant_counts == [0]
    release.set()
    assert await asyncio.gather(one, two) == ["done", "done"]
    assert seen_assistant_counts == [0, 1]


async def test_newer_event_started_first_is_deferred_without_spending_an_attempt(maker, monkeypatch):
    await debounce_off(maker)
    first = await add_event(maker, "test:reverse", "reverse-1", "first question")
    second = await add_event(maker, "test:reverse", "reverse-2", "second question")
    queue = Queue()
    assert await process_event(maker, second, queue=queue) == "queued"
    async with maker() as session:
        newer = await session.get(WebhookEvent, second)
        assert newer.status == "queued" and newer.attempts == 0
    assert queue.jobs and queue.jobs[0][2]["_defer_by"].total_seconds() > 0
    assert await process_event(maker, first) == "done"
    assert await process_event(maker, second) == "done"


async def test_lock_timeout_requeues_without_counting_a_failed_attempt(maker, monkeypatch):
    event_id = await add_event(maker, "test:locked", "locked-1", "waiting question")
    queue = Queue()
    monkeypatch.setattr(ingest, "EVENT_LOCK_TIMEOUT_S", 0.15)
    key = "messenger\x1ftest:locked"
    async with maker.kw["bind"].connect() as blocker:
        await blocker.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": key})
        await blocker.commit()
        try:
            assert await process_event(maker, event_id, queue=queue) == "queued"
        finally:
            await blocker.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key})
            await blocker.commit()
    async with maker() as session:
        event = await session.get(WebhookEvent, event_id)
        assert event.status == "queued" and event.attempts == 0 and event.error is None
    assert queue.jobs and queue.jobs[0][0] == "process_event_job"


async def test_different_guests_still_process_in_parallel(maker, monkeypatch):
    await debounce_off(maker)
    a = await add_event(maker, "test:user-a", "parallel-a", "question for a")
    b = await add_event(maker, "test:user-b", "parallel-b", "question for b")
    both_entered = asyncio.Event()
    release = asyncio.Event()
    entered = 0

    async def fake_reply(session, r, question, **kw):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await release.wait()
        return ReplyResult(f"answer {question}", "llm", debug={})

    monkeypatch.setattr(ingest, "generate_reply", fake_reply)
    tasks = [asyncio.create_task(process_event(maker, a)), asyncio.create_task(process_event(maker, b))]
    await asyncio.wait_for(both_entered.wait(), timeout=2)
    release.set()
    assert await asyncio.gather(*tasks) == ["done", "done"]


async def test_crash_releases_guest_lock(maker, monkeypatch):
    await debounce_off(maker)
    event_id = await add_event(maker, "test:crash", "crash-1", "question after crash")
    real = ingest.handle_message
    calls = 0

    async def crash_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated crash")
        return await real(*args, **kwargs)

    monkeypatch.setattr(ingest, "handle_message", crash_once)
    assert await process_event(maker, event_id) == "queued"
    assert await asyncio.wait_for(process_event(maker, event_id), timeout=2) == "done"
