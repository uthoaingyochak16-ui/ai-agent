from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountStat(Base):
    """One day of a Page's / Instagram account's audience numbers (followers, reach ...), kept for the trend
    (services/social.py)."""
    __tablename__ = "social_account_stats"
    __table_args__ = (UniqueConstraint("day", "channel", "account_id", name="uq_account_stat_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(64))
    followers: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)


class SocialReport(Base):
    """The AI's weekly report of the social channels (services/social.py), also sent to the alert webhook."""
    __tablename__ = "social_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    sent: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Broadcast(Base):
    """A WhatsApp message to many guests at once, with an approved template (services/broadcast.py). Only guests who
    agreed to be contacted (consent) and wrote to us on WhatsApp are in the audience."""
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    template: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(16), default="bn")
    params: Mapped[list] = mapped_column(JSONB, default=list)          # template body values, in order
    preview: Mapped[str | None] = mapped_column(Text)                  # what the template says (for staff and the Inbox)
    audience: Mapped[dict] = mapped_column(JSONB, default=dict)        # {"account_id": ..., "active_days": ...}
    # draft | sending | done | cancelled | failed
    status: Mapped[str] = mapped_column(String(16), default="draft")
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (UniqueConstraint("broadcast_id", "identity_id", name="uq_broadcast_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey("channel_identities.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | sent | failed | skipped
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdDraft(Base):
    """An ad the AI prepared (audience, budget, text), for a person to review and start in Meta Ads Manager. Nothing
    here spends money (services/ads.py)."""
    __tablename__ = "ad_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    objective: Mapped[str] = mapped_column(String(40), default="engagement")   # awareness | engagement | messages | traffic | leads
    post_id: Mapped[int | None] = mapped_column(ForeignKey("social_posts.id", ondelete="SET NULL"))
    program_id: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[dict] = mapped_column(JSONB, default=dict)       # {"age_min", "age_max", "genders", "locations", "interests"}
    daily_budget: Mapped[float | None] = mapped_column(Numeric(12, 2))  # in the ad account's currency (BDT)
    days: Mapped[int | None] = mapped_column(Integer)
    # draft | approved | started | archived
    status: Mapped[str] = mapped_column(String(16), default="draft")
    note: Mapped[str | None] = mapped_column(Text)                     # the AI's reasons / staff's notes
    created_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
