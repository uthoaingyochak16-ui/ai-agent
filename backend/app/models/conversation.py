from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"), index=True)
    channel_identity_id: Mapped[int] = mapped_column(ForeignKey("channel_identities.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | handover | closed
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handover_reason: Mapped[str | None] = mapped_column(String(120))
    summary: Mapped[str | None] = mapped_column(Text)  # rolling conversation summary
    summary_upto_message_id: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_id", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | agent | system
    content_redacted: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    sources_json: Mapped[dict | list | None] = mapped_column(JSONB)
    attachments: Mapped[list | None] = mapped_column(JSONB)
    external_message_id: Mapped[str | None] = mapped_column(String(160), index=True)
    delivery_status: Mapped[str | None] = mapped_column(String(16))  # sent | failed | blocked
    flag: Mapped[str | None] = mapped_column(String(32))             # e.g. out_of_window
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
