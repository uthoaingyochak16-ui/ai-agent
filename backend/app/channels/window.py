"""Messaging-window rules.

Messenger / Instagram: free-form replies within 24h of the user's last message; up to 7 days only with the
HUMAN_AGENT tag (a human replying), which must be enabled for the app.
WhatsApp: free-form within 24h (customer service window); outside it only approved template messages.
Meta may change these rules; the numbers live here in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STANDARD = timedelta(hours=24)
HUMAN_AGENT = timedelta(days=7)


@dataclass(frozen=True)
class WindowState:
    open: bool
    expires_at: datetime | None
    needs_template: bool = False       # WhatsApp outside the window
    needs_human_agent_tag: bool = False  # Messenger/IG between 24h and 7d, human replies only


def window_state(channel: str, last_user_message_at: datetime | None, *, now: datetime | None = None,
                 sender_is_human: bool = False, human_agent_enabled: bool = False) -> WindowState:
    now = now or datetime.now(timezone.utc)
    if last_user_message_at is None:
        return WindowState(False, None, needs_template=(channel == "whatsapp"))
    if last_user_message_at.tzinfo is None:
        last_user_message_at = last_user_message_at.replace(tzinfo=timezone.utc)
    std_end = last_user_message_at + STANDARD
    if now <= std_end:
        return WindowState(True, std_end)
    if channel in ("messenger", "instagram") and sender_is_human and human_agent_enabled:
        ha_end = last_user_message_at + HUMAN_AGENT
        if now <= ha_end:
            return WindowState(True, ha_end, needs_human_agent_tag=True)
    return WindowState(False, std_end, needs_template=(channel == "whatsapp"))
