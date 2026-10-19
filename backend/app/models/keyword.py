from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KeywordEntry(Base):
    """The keyword index. One row per (keyword stem, record): which keywords describe which chunk / Q&A / variable /
    program, and how strongly (title 3.0, file-given keywords 2.5, document title 1.5, body 1.0). It is kept in step
    with the data automatically (app/models/keyword_hooks.py) and rebuilt nightly (rag/keyword_index.py)."""
    __tablename__ = "keyword_entries"
    __table_args__ = (UniqueConstraint("source_type", "source_ref", "stem", name="uq_kw_source_stem"),
                      Index("ix_kw_stem", "stem"), Index("ix_kw_source", "source_type", "source_ref"),
                      Index("ix_kw_document", "document_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    stem: Mapped[str] = mapped_column(String(64))          # inflection-free form: প্রেমের / প্রেমে / প্রেম share one stem
    term: Mapped[str] = mapped_column(String(64))          # a readable surface form of it
    source_type: Mapped[str] = mapped_column(String(12))   # chunk | qa | variable | program
    source_ref: Mapped[str] = mapped_column(String(160))   # the record's id (a variable's key)
    document_id: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(12), default="body", server_default="body")  # title|file|doc|body|signature|phrase|ai|manual|unique


class RecordKeywords(Base):
    """Keywords somebody added to a record (by hand, with the AI button, or by the uniqueness check) and automatic
    keywords somebody removed. The automatic ones are regenerated from the text on every change, so only these edits
    need to be stored."""
    __tablename__ = "record_keywords"
    __table_args__ = (UniqueConstraint("source_type", "source_ref", name="uq_record_keywords_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(12))
    source_ref: Mapped[str] = mapped_column(String(160))
    added: Mapped[list] = mapped_column(JSONB, default=list)     # [{"k": "ফি", "origin": "manual" | "ai" | "unique"}]
    removed: Mapped[list] = mapped_column(JSONB, default=list)   # stems of automatic keywords switched off
    ai_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
