from __future__ import annotations

from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        # A phone/email may belong to exactly one *active* profile. Merged profiles have the hashes cleared.
        Index("uq_profiles_phone_hash", "phone_hash", unique=True, postgresql_where="phone_hash IS NOT NULL"),
        Index("uq_profiles_email_hash", "email_hash", unique=True, postgresql_where="email_hash IS NOT NULL"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(160))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    phone_enc: Mapped[str | None] = mapped_column(Text)
    email_hash: Mapped[str | None] = mapped_column(String(64))
    email_enc: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    age: Mapped[int | None] = mapped_column(Integer)
    profession: Mapped[str | None] = mapped_column(String(160))
    language: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lead_asks: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")  # 0 never, 1 asked, 2 reminded: stop
    # name/phone request (services/lead.py): not_asked | asked | provided | declined | deferred. Asked at most once;
    # after declined/deferred only a real action (callback, enrolment) may ask again, once, optionally.
    personal_info_request_status: Mapped[str] = mapped_column(String(12), default="not_asked", server_default="not_asked")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"), index=True)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    identities: Mapped[list["ChannelIdentity"]] = relationship(back_populates="profile")
    facts: Mapped[list["ProfileFact"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (UniqueConstraint("channel", "external_id", name="uq_channel_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # messenger | instagram | whatsapp
    external_id: Mapped[str] = mapped_column(String(128))
    # the Page / Instagram account / WhatsApp number the guest last wrote to: replies go out through it
    account_id: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(160))
    avatar_url: Mapped[str | None] = mapped_column(Text)  # Meta CDN URL; expires, so it is refreshed periodically
    profile_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_user_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile: Mapped[Profile | None] = relationship(back_populates="identities")


class ProfileFact(Base):
    __tablename__ = "profile_facts"
    __table_args__ = (UniqueConstraint("profile_id", "key", name="uq_fact_profile_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)  # keys starting with "pending_" hold AES-GCM ciphertext
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str | None] = mapped_column(String(16))  # ai | staff (staff values are never overwritten by the AI)
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", use_alter=True, name="fk_fact_source_message"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    profile: Mapped[Profile] = relationship(back_populates="facts")


class FamilyMember(Base):
    """One relative the guest talked about ("amar ma", "my son Rafi"): kept apart from the guest's own details,
    so it is always clear whose age / blood group / problem it is."""
    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(40))          # mother | father | brother | son | ... (English)
    name: Mapped[str | None] = mapped_column(String(160))
    age: Mapped[int | None] = mapped_column(Integer)
    blood_group: Mapped[str | None] = mapped_column(String(4))  # A+ ... O-
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"problem": "diabetes", "school": "...", ...}
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai | staff (the AI only fills gaps in staff rows)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProfileMergeLog(Base):
    __tablename__ = "profile_merge_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    kept_profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    merged_profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(64), default="manual")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverted_by: Mapped[str | None] = mapped_column(String(64))
