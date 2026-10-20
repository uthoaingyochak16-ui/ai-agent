from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (ai_admin, auth, cache_admin, channels_admin, conversations, documents, evaluation,
                     health, keywords, leads, profiles_admin, programs, qa, stats, teach, test_chat, webhooks)
from app.core.config import get_settings
from app.core.logging import request_id_var, setup_logging
from app.db.session import get_sessionmaker
from app.services.embedding_maintenance import embedding_status

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    app.state.queue = await create_pool(RedisSettings.from_dsn(s.redis_url))
    try:
        async with get_sessionmaker()() as session:
            state = await embedding_status(session)
            if state["hash_warning"]:
                logger.warning("PRODUCTION WARNING: hash embeddings are active while a real channel is configured; "
                               "Bangla/Banglish semantic retrieval will be weak")
    except Exception:  # migrations/health reporting will expose database problems; do not prevent startup
        logger.exception("could not inspect embedding safety at startup")
    yield
    await app.state.queue.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Multi-channel AI Assistant", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    for r in (health.router, webhooks.router, auth.router, conversations.router, profiles_admin.router,
              channels_admin.router, documents.router, qa.router, ai_admin.router, stats.router, test_chat.router,
              evaluation.router, cache_admin.router, programs.router, keywords.router, teach.router, leads.router):
        app.include_router(r)
    return app


app = create_app()
