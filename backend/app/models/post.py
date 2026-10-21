from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SocialPost(Base):
    """A post on a Facebook Page or an Instagram account (services/posts.py): written and scheduled here (source "app")
    or read from the platform (source "synced"), with its performance numbers for the analysis."""
    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_post_channel_external"),
                      Index("ix_social_posts_status_when", "status", "scheduled_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))                    # facebook | instagram
    account_id: Mapped[str] = mapped_column(String(64))                 # Page id / Instagram account id
    external_id: Mapped[str | None] = mapped_column(String(160))        # the platform's post id once published
    source: Mapped[str] = mapped_column(String(12), default="app")      # app | synced
    # draft | pending_approval | scheduled | publishing | published | failed
    status: Mapped[str] = mapped_column(String(16), default="draft")
    message: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)                 # public image (Instagram needs one)
    media_type: Mapped[str | None] = mapped_column(String(24))          # text | photo | link | video | reel | carousel
    permalink: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(60))               # the AI's one-topic label, for the analysis
    ai_written: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)          # reach, reactions, comments, shares, saves, clicks
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    program_id: Mapped[int | None] = mapped_column(Integer, index=True)    # the program the post is about (reminder drafts)
    suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # the AI's suggested time (drafts)
    note: Mapped[str | None] = mapped_column(Text)                          # why the AI suggests it / staff's note
    ab_group: Mapped[str | None] = mapped_column(String(32), index=True)    # A/B test: posts of one test share it
    ab_variant: Mapped[str | None] = mapped_column(String(4))               # "A", "B" ...
    history: Mapped[list] = mapped_column(JSONB, default=list)              # [{"at", "by", "action"}]
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
