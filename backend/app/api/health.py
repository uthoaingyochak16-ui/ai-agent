from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(response: Response, session: AsyncSession = Depends(get_session)):
    status = {"db": False, "redis": False}
    try:
        await session.execute(text("SELECT 1"))
        status["db"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        status["redis"] = bool(await get_redis().ping())
    except Exception:  # noqa: BLE001
        pass
    if not all(status.values()):
        response.status_code = 503
    return status
