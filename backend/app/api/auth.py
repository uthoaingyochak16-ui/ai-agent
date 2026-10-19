from __future__ import annotations

import time

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models import AdminUser

router = APIRouter(prefix="/api/auth", tags=["auth"])
SSO_ONLY = "!sso"   # password hash of a user created by single sign-on: no password ever matches it


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(AdminUser).where(AdminUser.email == body.email.lower().strip()))).scalar_one_or_none()
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role}}


class SSOIn(BaseModel):
    token: str


@router.post("/sso", dependencies=[Depends(rate_limit("sso", 30, 60))])
async def sso(body: SSOIn, session: AsyncSession = Depends(get_session)):
    """Sign in a user of the main application (docs/INTEGRATION.md). The main app signs a short-lived HS256 token with
    the shared secret: {iss, sub, email, name, role, iat, exp, jti}. The user is created on first use; name and role
    follow the main app on every sign-in; a user deactivated here stays out. Each token works once."""
    s = get_settings()
    if not s.sso_shared_secret:
        raise HTTPException(404, "Single sign-on is not configured")
    try:
        claims = jwt.decode(body.token, s.sso_shared_secret, algorithms=["HS256"], issuer=s.sso_issuer or None,
                            options={"require": ["exp", "iat", "jti", "email"], "verify_iss": bool(s.sso_issuer)})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid sign-in token: {exc}") from exc
    if time.time() - float(claims["iat"]) > s.sso_max_age_s:
        raise HTTPException(401, "Sign-in token is too old")
    if not await get_redis().set(f"sso:jti:{claims['jti']}", "1", nx=True, ex=s.sso_max_age_s + 60):
        raise HTTPException(401, "Sign-in token was already used")

    email = str(claims["email"]).lower().strip()
    role = claims.get("role") if claims.get("role") in ("admin", "agent") else s.sso_default_role
    name = str(claims.get("name") or "")[:120]
    user = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if user is None:
        user = AdminUser(email=email, name=name, role=role, password_hash=SSO_ONLY)
        session.add(user)
    elif not user.active:
        raise HTTPException(403, "This user is deactivated in the assistant")
    else:
        user.role, user.name = role, name or user.name
    await session.commit()
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role}}


@router.get("/me")
async def me(user: AdminUser = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
