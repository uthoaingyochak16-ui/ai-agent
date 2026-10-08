from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LLMProviderConfig(Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # anthropic | openai | google
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(String(255))
    allow_pii: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_s: Mapped[float] = mapped_column(Float, default=30.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LLMModel(Base):
    """Price table (USD per 1M tokens). Editable so cost estimates stay accurate."""
    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("provider", "model", name="uq_llm_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    input_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    output_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0)
    price_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LLMRoute(Base):
    """Per-task routing, editable from the dashboard: answer | answer_large | extraction | summarization."""
    __tablename__ = "llm_routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(32), unique=True)
    primary_provider: Mapped[str] = mapped_column(String(32))
    primary_model: Mapped[str] = mapped_column(String(96))
    fallback_provider: Mapped[str | None] = mapped_column(String(32))
    fallback_model: Mapped[str | None] = mapped_column(String(96))
    max_tokens: Mapped[int] = mapped_column(Integer, default=400)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)


class LLMCall(Base):
    """One row per provider attempt: cost log AND outgoing-request audit (exactly what was sent, post-filter)."""
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    task: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str | None] = mapped_column(String(16))
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(8), default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    budget_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    redactions: Mapped[list | None] = mapped_column(JSONB)
    request_json: Mapped[dict | None] = mapped_column(JSONB)
