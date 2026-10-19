from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Program(Base):
    """An upcoming course / event. The assistant answers "kobe / koto / kothay" questions about programs from here;
    a program disappears the day after its last date (rag/programs.py purge_expired)."""
    __tablename__ = "programs"
    __table_args__ = (Index("ix_programs_start_date", "start_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    date_text: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)     # last day; empty = one-day program
    fee: Mapped[str | None] = mapped_column(Text)     # free text: "১১,৫০০ টাকা", "Free"
    place: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def last_day(self) -> date:
        return self.end_date or self.start_date
