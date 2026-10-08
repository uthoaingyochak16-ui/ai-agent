from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebhookEvent(Base):
    """Durable inbox. UNIQUE(channel, external_id) is the dedup mechanism."""
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_event_channel_external"),
        Index("ix_events_status_received", "status", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSONB)  # normalized message
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|processing|done|failed
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChannelConfig(Base):
    __tablename__ = "channel_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    account_id: Mapped[str | None] = mapped_column(String(64))       # page id / ig user id / phone_number_id
    waba_id: Mapped[str | None] = mapped_column(String(64))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    app_secret_enc: Mapped[str | None] = mapped_column(Text)         # overrides META_APP_SECRET for this channel
    verify_token: Mapped[str | None] = mapped_column(String(128))    # overrides META_VERIFY_TOKEN
    status: Mapped[str] = mapped_column(String(16), default="unconfigured")
    status_detail: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
