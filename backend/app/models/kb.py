from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.db.base import Base

DIM = get_settings().embedding_dim
_HNSW = dict(postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64})


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(8))  # pdf | txt | csv | docx
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|processing|ready|failed|superseded
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_embedding", "embedding", postgresql_ops={"embedding": "vector_cosine_ops"}, **_HNSW),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[str | None] = mapped_column(Text)  # the record's question, for Q&A-shaped files
    content: Mapped[str] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(DIM))


class QAPair(Base):
    __tablename__ = "qa_pairs"
    __table_args__ = (Index("ix_qa_embedding", "embedding", postgresql_ops={"embedding": "vector_cosine_ops"}, **_HNSW),)

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    question_norm: Mapped[str] = mapped_column(Text, index=True)
    answer_template: Mapped[str] = mapped_column(Text)  # supports {variable} placeholders
    category: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(DIM))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Variable(Base):
    __tablename__ = "variables"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)  # e.g. "কোয়ান্টাম মেথড কোর্সের ফি": how guests ask for it
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SemanticCache(Base):
    """Only PII-free question/answer pairs are cached, so one user's answer can never carry another's data."""
    __tablename__ = "semantic_cache"
    __table_args__ = (Index("ix_cache_embedding", "embedding", postgresql_ops={"embedding": "vector_cosine_ops"}, **_HNSW),)

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(DIM))
    context_version: Mapped[int] = mapped_column(Integer)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)  # redacted
    channel: Mapped[str | None] = mapped_column(String(16))
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(24), default="no_context")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
