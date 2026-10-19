"""Single sign-on from the main application: one-time HS256 tokens signed with the shared secret."""
import time
import uuid

import jwt
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AdminUser

SECRET = "test-shared-secret-of-a-decent-length-0123456789"


@pytest.fixture
def sso(monkeypatch, _fake_redis):
    s = get_settings()
    monkeypatch.setattr(s, "sso_shared_secret", SECRET)
    monkeypatch.setattr(s, "sso_issuer", "main-app")
    monkeypatch.setattr("app.api.auth.get_redis", lambda: _fake_redis)


def token(*, secret=SECRET, iss="main-app", age=0, ttl=120, **claims):
    now = int(time.time()) - age
    body = {"iss": iss, "sub": "42", "email": "Rahim@Example.org", "name": "Rahim", "iat": now, "exp": now + ttl,
            "jti": uuid.uuid4().hex, **claims}
    return jwt.encode(body, secret, algorithm="HS256")


async def test_sso_is_off_without_a_shared_secret(client):
    assert (await client.post("/api/auth/sso", json={"token": token()})).status_code == 404


async def test_a_main_app_user_is_signed_in_and_created_once(client, maker, sso):
    r = await client.post("/api/auth/sso", json={"token": token(role="admin")})
    assert r.status_code == 200 and r.json()["user"] == {**r.json()["user"], "email": "rahim@example.org", "role": "admin"}
    me = await client.get("/api/auth/me", headers={"authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "rahim@example.org"
    r2 = await client.post("/api/auth/sso", json={"token": token(name="Rahim Uddin")})   # no role: the default (agent)
    assert r2.json()["user"]["id"] == r.json()["user"]["id"] and r2.json()["user"]["role"] == "agent"
    async with maker() as s:
        users = (await s.execute(select(AdminUser).where(AdminUser.email == "rahim@example.org"))).scalars().all()
        assert len(users) == 1 and users[0].name == "Rahim Uddin"
    # a user made by SSO has no password: the password form never lets them in
    assert (await client.post("/api/auth/login", json={"email": "rahim@example.org", "password": "!sso"})).status_code == 401


@pytest.mark.parametrize("bad", [
    {"secret": "another-secret-of-a-decent-length-0123456789"},   # not signed by the main app
    {"iss": "someone-else"},
    {"age": 3600, "ttl": 7200},                                   # issued an hour ago
    {"ttl": -10},                                                 # expired
])
async def test_bad_tokens_are_refused(client, sso, bad):
    assert (await client.post("/api/auth/sso", json={"token": token(**bad)})).status_code == 401


async def test_a_token_works_once_and_a_deactivated_user_stays_out(client, maker, sso):
    t = token()
    assert (await client.post("/api/auth/sso", json={"token": t})).status_code == 200
    assert (await client.post("/api/auth/sso", json={"token": t})).status_code == 401      # replayed
    async with maker() as s:
        (await s.execute(select(AdminUser).where(AdminUser.email == "rahim@example.org"))).scalar_one().active = False
        await s.commit()
    assert (await client.post("/api/auth/sso", json={"token": token()})).status_code == 403
