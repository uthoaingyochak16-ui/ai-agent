"""Meta webhooks: verify -> signature check -> durable insert (dedup) -> enqueue -> 200."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.registry import CHANNELS, get_adapter
from app.channels.signature import verify_signature
from app.core import crypto
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.db.session import get_session
from app.models import ChannelConfig, WebhookEvent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _secrets(session: AsyncSession, channel: str) -> tuple[str, str]:
    s = get_settings()
    cfg = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel == channel))).scalar_one_or_none()
    app_secret = s.meta_app_secret
    verify = s.meta_verify_token
    if cfg:
        if cfg.app_secret_enc:
            app_secret = crypto.decrypt(cfg.app_secret_enc, aad=f"secret:{channel}")
        if cfg.verify_token:
            verify = cfg.verify_token
    return app_secret, verify


def _adapter(channel: str):
    if channel not in CHANNELS:
        raise HTTPException(404, "Unknown channel")
    return get_adapter(channel)


@router.get("/{channel}", dependencies=[Depends(rate_limit("wh-verify", 60, 60))])
async def verify(channel: str, request: Request, session: AsyncSession = Depends(get_session)):
    adapter = _adapter(channel)
    _, verify_token = await _secrets(session, channel)
    params = dict(request.query_params)
    challenge = adapter.verify_webhook(params, verify_token)
    if challenge is None:
        given = (params.get("hub.verify_token") or "").strip()
        reason = ("no verify token is saved for this channel" if not verify_token
                  else f"Meta sent a verify token starting '{given[:4]}', but the one saved here starts '{verify_token[:4]}'"
                  if given != verify_token.strip() else "unexpected hub.mode")
        logger.warning("webhook verification failed: %s", reason, extra={"ctx": {"channel": channel}})
        cfg = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel == channel))).scalar_one_or_none()
        if cfg is not None:  # surface it on the Channels page, where the admin is looking
            cfg.status = "error"
            cfg.status_detail = (f"Meta's webhook verification failed: {reason}. Put exactly the same verify token "
                                 "in the Meta app and here, save, then click 'Verify and save' in Meta again.")
            await session.commit()
        raise HTTPException(403, "Verification failed")
    if given_ok := params.get("hub.verify_token"):  # clear an earlier failure note
        cfg = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel == channel))).scalar_one_or_none()
        if cfg is not None and cfg.status_detail and cfg.status_detail.startswith("Meta's webhook verification failed"):
            cfg.status, cfg.status_detail = "configured", f"Webhook verified by Meta ({given_ok[:4]}…). Click Test connection."
            await session.commit()
    return Response(content=challenge, media_type="text/plain")


@router.post("/{channel}", dependencies=[Depends(rate_limit("wh-post", 600, 60))])
async def receive(channel: str, request: Request, session: AsyncSession = Depends(get_session)):
    adapter = _adapter(channel)
    body = await request.body()
    app_secret, _ = await _secrets(session, channel)
    if not verify_signature(body, request.headers.get("x-hub-signature-256"), app_secret):
        reason = "no app secret configured (set META_APP_SECRET or the channel's App secret)" if not app_secret \
            else "missing signature header" if not request.headers.get("x-hub-signature-256") \
            else "signature mismatch (wrong app secret?)"
        logger.warning("webhook signature rejected: %s", reason, extra={"ctx": {"channel": channel}})
        raise HTTPException(403, "Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Invalid JSON") from None

    new_ids: list[tuple[int, int]] = []
    for m in adapter.parse_incoming(payload):
        row = await session.execute(pg_insert(WebhookEvent).values(
            channel=channel, external_id=m.message_id, payload=m.to_json())
            .on_conflict_do_nothing(constraint="uq_event_channel_external").returning(WebhookEvent.id))
        got = row.first()
        if got:  # duplicates (Meta retries) insert nothing and are not re-enqueued
            new_ids.append((got[0], 0))
    await session.commit()

    queue = getattr(request.app.state, "queue", None)
    for event_id, attempt in new_ids:
        try:
            await queue.enqueue_job("process_event_job", event_id, _job_id=f"evt-{event_id}-{attempt}")
        except Exception:  # noqa: BLE001 - the row is durable; the requeue cron will pick it up
            logger.exception("enqueue failed for event %s", event_id)
    return {"status": "ok", "accepted": len(new_ids)}
