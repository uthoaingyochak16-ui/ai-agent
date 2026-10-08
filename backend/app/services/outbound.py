"""Sending replies (AI or human) with window enforcement; every attempt is recorded as a message row."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime, timezone

from app.channels.base import ChannelCredentials, OutOfWindowError, TemplateMessage
from app.channels.graph import GraphError
from app.channels.registry import get_adapter
from app.channels.window import window_state
from app.core import crypto
from app.core.config import get_settings
from app.models import ChannelConfig, ChannelIdentity, Conversation, Message
from app.privacy.redaction import redact_for_storage
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
TEST_PREFIX = "test:"


class ChannelNotConfigured(Exception):
    pass


class SendFailed(Exception):
    pass


async def get_credentials(session: AsyncSession, channel: str) -> ChannelCredentials:
    cfg = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel == channel))).scalar_one_or_none()
    if cfg is None or not cfg.enabled or not cfg.access_token_enc:
        raise ChannelNotConfigured(channel)
    s = get_settings()
    return ChannelCredentials(access_token=crypto.decrypt(cfg.access_token_enc, aad=f"token:{channel}"),
                              account_id=cfg.account_id, base_url=s.graph_base_url, version=s.graph_api_version)


async def send_to_conversation(session: AsyncSession, conv: Conversation, text: str, *, sender_role: str = "assistant",
                               template: TemplateMessage | None = None, tokens_in: int | None = None,
                               tokens_out: int | None = None, sources: dict | list | None = None) -> Message:
    """sender_role: 'assistant' (AI) or 'agent' (human from the dashboard).
    Blocked sends (window closed, no template) are stored with delivery_status='blocked', flag='out_of_window'
    and raise OutOfWindowError so the caller can tell the user/agent."""
    ident = await session.get(ChannelIdentity, conv.channel_identity_id)
    if ident.external_id.startswith(TEST_PREFIX):  # Test Chat: same code path, but nothing leaves the building
        msg = Message(conversation_id=conv.id, role=sender_role, content_redacted=redact_for_storage(text), tokens_in=tokens_in,
                      tokens_out=tokens_out, sources_json=sources, delivery_status="simulated")
        session.add(msg)
        conv.last_message_at = datetime.now(timezone.utc)
        await session.flush()
        return msg
    adapter = get_adapter(ident.channel)
    human_ok = bool(await get_setting(session, "human_agent_tag_enabled"))
    ws = window_state(ident.channel, ident.last_user_message_at, sender_is_human=(sender_role == "agent"),
                      human_agent_enabled=human_ok)
    content = redact_for_storage(text if template is None else f"[template:{template.name}] {text}".strip())
    msg = Message(conversation_id=conv.id, role=sender_role, content_redacted=content, tokens_in=tokens_in,
                  tokens_out=tokens_out, sources_json=sources)
    session.add(msg)

    if template is not None and ident.channel != "whatsapp":
        msg.delivery_status, msg.flag = "blocked", "template_unsupported"
        await session.flush()
        raise SendFailed("template messages are only supported on WhatsApp")
    if not ws.open and template is None:
        msg.delivery_status, msg.flag = "blocked", "out_of_window"
        await session.flush()
        raise OutOfWindowError(ident.channel, needs_template=ws.needs_template)

    try:
        creds = await get_credentials(session, ident.channel)
        res = await adapter.send_message(ident.external_id, text, creds, template=template,
                                         human_agent_tag=ws.needs_human_agent_tag)
        msg.external_message_id, msg.delivery_status = res.message_id, "sent"
    except OutOfWindowError:
        msg.delivery_status, msg.flag = "blocked", "out_of_window"
        await session.flush()
        raise
    except (ChannelNotConfigured, GraphError) as exc:
        msg.delivery_status, msg.flag = "failed", type(exc).__name__[:32]
        logger.error("send failed on %s: %s", ident.channel, exc)
        await session.flush()
        raise SendFailed(str(exc)) from exc
    conv.last_message_at = datetime.now(timezone.utc)
    await session.flush()
    return msg
