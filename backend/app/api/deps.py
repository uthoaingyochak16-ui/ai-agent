from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session
from app.models import AdminUser

_bearer = HTTPBearer(auto_error=False)


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                       session: AsyncSession = Depends(get_session)) -> AdminUser:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        data = decode_token(creds.credentials)
        user = await session.get(AdminUser, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid token") from None
    if user is None or not user.active:
        raise HTTPException(401, "Account disabled")
    return user


def require_role(*roles: str):
    async def dep(user: AdminUser = Depends(current_user)) -> AdminUser:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return dep


any_staff = require_role("admin", "agent")
admin_only = require_role("admin")
