from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DataSheet(Base):
    """A table of facts staff keep like a spreadsheet: Books (name, edition, author, publication, price, description),
    Branches, Products ... (services/sheets.py). The AI finds its rows through the keyword index and the search_sheet
    tool; `description` tells it what the sheet is for."""
    __tablename__ = "data_sheets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    columns: Mapped[list] = mapped_column(JSONB, default=list)        # [{"key": "author", "label": "Author"}, ...]
    active: Mapped[bool] = mapped_column(Boolean, default=True)       # off: the AI does not use it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataRow(Base):
    __tablename__ = "data_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("data_sheets.id", ondelete="CASCADE"), index=True)
    values: Mapped[dict] = mapped_column(JSONB, default=dict)          # {column key: text}
    search_text: Mapped[str] = mapped_column(Text, default="")         # every value, lower case (word search)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
