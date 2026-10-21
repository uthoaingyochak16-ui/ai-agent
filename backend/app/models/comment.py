from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PostComment(Base):
    """A comment on one of the Page's posts (services/comments.py). The comment id is the dedup key (Meta retries)."""
    __tablename__ = "post_comments"
    __table_args__ = (Index("ix_post_comments_status", "status", "received_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), default="facebook")
    page_id: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[str] = mapped_column(String(160), index=True)
    comment_id: Mapped[str] = mapped_column(String(160), unique=True)
    parent_id: Mapped[str | None] = mapped_column(String(160))         # the post, or the comment this one replies to
    from_id: Mapped[str | None] = mapped_column(String(64))
    from_name: Mapped[str | None] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text, default="")
    post_text: Mapped[str | None] = mapped_column(Text)                # what the post says, for the AI
    post_url: Mapped[str | None] = mapped_column(Text)                 # Instagram permalink (Facebook links are built from ids)
    source: Mapped[str] = mapped_column(String(12), default="webhook")  # webhook | scan (staff read older comments)
    # queued | processing | draft (staff approve) | replied | liked | needs_staff | ignored | hidden | deleted | failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # question | praise | greeting | reaction | complaint | against (attacks the organisation) | spam | abuse | other
    kind: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(Text)                   # the AI's one-line why, for staff
    confidence: Mapped[float | None] = mapped_column(Float)
    action: Mapped[str | None] = mapped_column(String(12))             # planned moderation: hide | delete | notify
    reply_text: Mapped[str | None] = mapped_column(Text)               # the AI's draft / what was posted
    reply_path: Mapped[str | None] = mapped_column(String(32))         # how the reply was made (llm, qa_shortcut, canned ...)
    reply_comment_id: Mapped[str | None] = mapped_column(String(160), index=True)
    liked: Mapped[bool] = mapped_column(Boolean, default=False)
    private_replied: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
