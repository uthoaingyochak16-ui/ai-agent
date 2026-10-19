from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only
from app.channels.graph import GraphError, graph_request
from app.channels.registry import CHANNELS
from app.core import crypto
from app.core.config import get_settings
from app.db.session import get_session
from app.models import ChannelConfig, WebhookEvent

router = APIRouter(prefix="/api/channels", tags=["channels"], dependencies=[Depends(admin_only)])


def _view(channel: str, cfg: ChannelConfig | None) -> dict:
    s = get_settings()
    return {"id": cfg.id if cfg else None, "label": cfg.label if cfg else None,
            "channel": channel, "webhook_url": f"{s.public_base_url.rstrip('/')}/webhooks/{channel}",
            "enabled": bool(cfg and cfg.enabled), "account_id": cfg.account_id if cfg else None,
            "waba_id": cfg.waba_id if cfg else None, "has_token": bool(cfg and cfg.access_token_enc),
            "has_app_secret": bool(cfg and cfg.app_secret_enc), "server_has_app_secret": bool(s.meta_app_secret),
            "verify_token": cfg.verify_token if cfg else None,
            "status": cfg.status if cfg else "unconfigured", "status_detail": cfg.status_detail if cfg else None,
            "last_checked_at": cfg.last_checked_at if cfg else None}


async def _accounts(session: AsyncSession, channel: str) -> list[ChannelConfig]:
    return list((await session.execute(select(ChannelConfig).where(ChannelConfig.channel == channel)
                                       .order_by(ChannelConfig.id))).scalars())


@router.get("")
async def list_channels(session: AsyncSession = Depends(get_session)):
    """One entry per channel; `accounts` lists every connected Page / account / number. The entry's own fields
    describe the first account, as they did when a channel had only one."""
    out = []
    for ch in CHANNELS:
        accs = await _accounts(session, ch)
        out.append(_view(ch, accs[0] if accs else None) | {"accounts": [_view(ch, a) for a in accs]})
    return out


class ChannelIn(BaseModel):
    label: str | None = Field(None, max_length=120)
    enabled: bool | None = None
    account_id: str | None = Field(None, max_length=64)
    waba_id: str | None = Field(None, max_length=64)
    access_token: str | None = None   # write-only
    app_secret: str | None = None     # write-only
    verify_token: str | None = Field(None, max_length=128)


async def _apply(session: AsyncSession, cfg: ChannelConfig, body: ChannelIn) -> dict:
    if body.account_id is not None and body.account_id.strip():
        clash = (await session.execute(select(ChannelConfig.id).where(
            ChannelConfig.channel == cfg.channel, ChannelConfig.account_id == body.account_id.strip(),
            ChannelConfig.id != (cfg.id or 0)))).first()
        if clash:
            raise HTTPException(409, "This Page / account / number is already connected")
    for f in ("label", "enabled", "account_id", "waba_id", "verify_token"):
        v = getattr(body, f)
        if v is not None:
            setattr(cfg, f, (v.strip() or None) if isinstance(v, str) and f in ("label", "account_id", "waba_id")
                    else v.strip() if isinstance(v, str) else v)
    if body.access_token:
        cfg.access_token_enc = crypto.encrypt(body.access_token, aad=f"token:{cfg.channel}")
    if body.app_secret:
        cfg.app_secret_enc = crypto.encrypt(body.app_secret, aad=f"secret:{cfg.channel}")
    cfg.status = "configured" if cfg.access_token_enc else "unconfigured"
    await session.commit()
    return _view(cfg.channel, cfg)


async def _account(session: AsyncSession, account: int) -> ChannelConfig:
    cfg = await session.get(ChannelConfig, account)
    if cfg is None:
        raise HTTPException(404, "Unknown account")
    return cfg


@router.post("/{channel}/accounts")
async def add_account(channel: str, body: ChannelIn, session: AsyncSession = Depends(get_session)):
    """Connect another Facebook Page / Instagram account / WhatsApp number."""
    if channel not in CHANNELS:
        raise HTTPException(404, "Unknown channel")
    cfg = ChannelConfig(channel=channel)
    session.add(cfg)
    return await _apply(session, cfg, body)


@router.put("/accounts/{account}")
async def save_account(account: int, body: ChannelIn, session: AsyncSession = Depends(get_session)):
    return await _apply(session, await _account(session, account), body)


@router.delete("/accounts/{account}")
async def delete_account(account: int, session: AsyncSession = Depends(get_session)):
    """Disconnect it here. Its guests and conversations stay; replies to them go through another account of the same
    channel only if Meta allows it (a Page-scoped id works only with its own Page)."""
    await session.delete(await _account(session, account))
    await session.commit()
    return {"ok": True}


@router.post("/accounts/{account}/test")
async def test_account(account: int, session: AsyncSession = Depends(get_session)):
    return await _test(session, await _account(session, account))


@router.put("/{channel}")
async def save_channel(channel: str, body: ChannelIn, session: AsyncSession = Depends(get_session)):
    """The channel's first account (kept for single-account setups)."""
    if channel not in CHANNELS:
        raise HTTPException(404, "Unknown channel")
    accs = await _accounts(session, channel)
    cfg = accs[0] if accs else ChannelConfig(channel=channel)
    if not accs:
        session.add(cfg)
    return await _apply(session, cfg, body)


@router.post("/{channel}/test")
async def test_channel(channel: str, session: AsyncSession = Depends(get_session)):
    accs = await _accounts(session, channel)
    if not accs:
        raise HTTPException(409, "Save an access token first")
    return await _test(session, accs[0])


async def _test(session: AsyncSession, cfg: ChannelConfig) -> dict:
    channel = cfg.channel
    if not cfg.access_token_enc:
        raise HTTPException(409, "Save an access token first")
    s = get_settings()
    token = crypto.decrypt(cfg.access_token_enc, aad=f"token:{channel}")
    app_secret = crypto.decrypt(cfg.app_secret_enc, aad=f"secret:{channel}") if cfg.app_secret_enc else s.meta_app_secret
    status, problems, notes = "ok", [], []
    try:
        if channel == "whatsapp":
            info = await _graph(s, token, cfg.account_id or "me", {"fields": "display_phone_number,verified_name"})
            notes.append(", ".join(f"{k}={v}" for k, v in info.items() if k != "id"))
            if app_secret and not await _secret_matches(s, token, app_secret):
                problems.append("App secret does not belong to the app that issued this token: webhooks will be rejected.")
        else:
            last = (await session.execute(select(func.max(WebhookEvent.received_at)).where(
                WebhookEvent.channel == channel))).scalar_one_or_none()
            problems, notes = await _check_page(s, token, app_secret, cfg, last)
    except GraphError as exc:
        status, problems = "error", [exc.message]
    if problems and status == "ok":
        status = "error"
    cfg.status = status
    cfg.status_detail = " ".join(problems + notes)[:1000]
    cfg.last_checked_at = datetime.now(timezone.utc)
    await session.commit()
    return _view(channel, cfg)


async def _graph(s, token: str, path: str, params: dict | None = None, method: str = "GET") -> dict:
    return await graph_request(method, path, token=token, base_url=s.graph_base_url, version=s.graph_api_version,
                               params=params)


async def _secret_matches(s, token: str, app_secret: str) -> bool:
    """Graph rejects a wrong appsecret_proof, so this proves the secret belongs to the token's app."""
    proof = hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    try:
        await _graph(s, token, "me", {"fields": "id", "appsecret_proof": proof})
        return True
    except GraphError:
        return False


async def _check_page(s, token: str, app_secret: str, cfg: ChannelConfig,
                      last_webhook: datetime | None = None) -> tuple[list[str], list[str]]:
    """Messenger / Instagram: is this a Page token, for the configured Page, with a matching app secret,
    and is the Page subscribed to the app's webhook? Subscribes it when the token is allowed to."""
    problems: list[str] = []
    me = await _graph(s, token, "me", {"fields": "id,name"})
    notes = [f"name={me.get('name')}"]
    if cfg.account_id and str(me.get("id")) != cfg.account_id.strip():
        problems.append(f"Token is for the Page \"{me.get('name')}\" (ID {me.get('id')}), but the Page ID saved here is "
                        f"{cfg.account_id}. Use a Page access token of the Page customers message, or change the Page ID.")
    if not app_secret:
        problems.append("No app secret: every incoming message is rejected.")
    elif not await _secret_matches(s, token, app_secret):
        problems.append("App secret does not belong to the app that issued this token: incoming messages are rejected.")
    fields = "messages,messaging_postbacks"
    try:
        subs = (await _graph(s, token, "me/subscribed_apps")).get("data", [])
        if not any("messages" in (a.get("subscribed_fields") or []) for a in subs):
            await _graph(s, token, "me/subscribed_apps", {"subscribed_fields": fields}, method="POST")
            notes.append("Page subscribed to the app's webhook (messages) just now.")
        else:
            notes.append("Page is subscribed to the webhook.")
    except GraphError as exc:
        if exc.code == 200 or "pages_manage_metadata" in exc.message:
            if last_webhook is not None:  # can't ask Meta, but messages are arriving, so the subscription works
                notes.append(f"Receiving messages (last one {last_webhook:%d %b %H:%M} UTC).")
            else:
                problems.append("No message has arrived yet, and the token lacks pages_manage_metadata, so the webhook "
                                "subscription can't be checked. In the Meta app: Messenger > Settings > Webhooks, click "
                                "'Add subscriptions' next to the Page and tick 'messages', then send the Page a test message.")
        else:
            problems.append(f"Webhook subscription check failed: {exc.message}")
    if last_webhook is not None and not any("Receiving" in n for n in notes):
        notes.append(f"Last message received {last_webhook:%d %b %H:%M} UTC.")
    return problems, notes
