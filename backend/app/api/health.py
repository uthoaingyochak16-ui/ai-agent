from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only
from app.core.redis import get_redis
from app.db.session import get_session
from app.services.monitoring import ai_metrics, system_health

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


@router.get("/api/health/deep", dependencies=[Depends(admin_only)])
async def deep(days: int = Query(14, ge=1, le=180), session: AsyncSession = Depends(get_session)):
    """Queue, delivery, LLM, budget and embedding health plus AI quality metrics (dashboard "System health" card)."""
    return {"system": await system_health(session), "ai": await ai_metrics(session, days)}
