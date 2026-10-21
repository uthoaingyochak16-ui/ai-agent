from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Lead(Base):
    """A guest who is interested in a program or in knowing more (services/leads.py). The lead list; staff send the
    ones they choose to the main application, where each becomes a guest + a task."""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    channel: Mapped[str | None] = mapped_column(String(16))
    interest: Mapped[str] = mapped_column(String(300))           # the program / service, in the guest's words
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    # new (on the list) | sent (staff sent it to the main app) | failed (sending failed; can be sent again) | dismissed
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    main_app_guest_id: Mapped[str | None] = mapped_column(String(64))
    main_app_task_id: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
