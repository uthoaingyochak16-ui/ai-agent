from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.ratelimit import rate_limit
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models import AdminUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


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


@router.get("/me")
async def me(user: AdminUser = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
