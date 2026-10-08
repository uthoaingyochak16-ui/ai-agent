import os

os.environ.update(
    SECRET_KEY="test-secret-key-0123456789abcdef0123456789",
    ENCRYPTION_KEY="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    HMAC_KEY="test-hmac-key-0123456789abcdef0123456789ab",
    META_APP_SECRET="testsecret",
    META_VERIFY_TOKEN="verify-me",
    DATABASE_URL="postgresql+asyncpg://app:app@localhost:5432/assistant_test",
    REDIS_URL="redis://localhost:6379/15",
    DEFAULT_PHONE_REGION="BD",
)

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.channels.base import IncomingMessage
from app.core import crypto
from app.db.base import Base
from app.db.session import get_session
from app.models import ChannelConfig

URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def schema():
    async def go():
        eng = create_async_engine(URL, poolclass=NullPool)
        async with eng.begin() as c:
            await c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await c.run_sync(Base.metadata.drop_all)
            await c.run_sync(Base.metadata.create_all)
        await eng.dispose()
    asyncio.run(go())


@pytest_asyncio.fixture
async def maker():
    eng = create_async_engine(URL, poolclass=NullPool)
    tables = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    async with eng.begin() as c:
        await c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(maker):
    async with maker() as s:
        yield s


class FakeRedis:
    def __init__(self):
        self.d: dict[str, int] = {}

    async def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    async def expire(self, k, s):
        return True


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple] = []
        self.ids: set[str] = set()

    async def enqueue_job(self, name, *args, _job_id=None, **kw):
        if _job_id in self.ids:
            return None
        self.ids.add(_job_id)
        self.jobs.append((name, args))
        return object()


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: fr)
    return fr


@pytest_asyncio.fixture
async def client(maker):
    from app.main import create_app
    app = create_app()

    async def _sess():
        async with maker() as s:
            yield s
    app.dependency_overrides[get_session] = _sess
    app.state.queue = FakeQueue()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


def mk_msg(channel="messenger", uid="u1", text="hello", mid="m1", ts=None, name=None):
    return IncomingMessage(channel=channel, external_user_id=uid, text=text, attachments=[],
                           timestamp=ts or datetime.now(timezone.utc), message_id=mid, sender_name=name)


async def configure_channel(session, channel="messenger", account_id="PAGE1"):
    session.add(ChannelConfig(channel=channel, enabled=True, account_id=account_id, status="ok",
                              access_token_enc=crypto.encrypt("TOKEN", aad=f"token:{channel}")))
    await session.commit()


async def seed_llm(session, *, providers=("anthropic",), allow_pii=None, routes=None, prices=None):
    """Create enabled providers, a price table and routes. routes: {task: (prov, model, fb_prov, fb_model)}"""
    from app.models import LLMModel, LLMProviderConfig, LLMRoute
    allow_pii = allow_pii or {}
    for p in providers:
        session.add(LLMProviderConfig(name=p, enabled=True, allow_pii=allow_pii.get(p, False),
                                      api_key_enc=crypto.encrypt(f"key-{p}", aad=f"llm:{p}")))
    for (p, m), (i, o) in (prices or {}).items():
        session.add(LLMModel(provider=p, model=m, input_price=i, output_price=o, price_verified=True))
    for task, (p, m, fp, fm) in (routes or {}).items():
        session.add(LLMRoute(task=task, primary_provider=p, primary_model=m, fallback_provider=fp, fallback_model=fm,
                             max_tokens=300, temperature=0.1))
    await session.commit()
