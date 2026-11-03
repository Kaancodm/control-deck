"""Metadata for the proposed isolated ``deck_kb.v1`` contract."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType

from app.kb.contracts import EMBEDDING_DIMENSION


class VectorType(UserDefinedType[Any]):
    """Reflect pgvector without adding a second array/numerical dependency."""

    cache_ok = True

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension

    def get_col_spec(self, **kw: Any) -> str:
        return f"VECTOR({self.dimension})"


ischema_names.setdefault("vector", VectorType)
metadata = MetaData(schema="deck_kb")

contract = Table(
    "contract",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", String(32), nullable=False),
    Column("embedding_model", String(64), nullable=False),
    Column("embedding_dimension", Integer, nullable=False),
    CheckConstraint("id = 1", name="ck_kb_contract_singleton"),
)

documents = Table(
    "documents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_task_id", UUID(as_uuid=True), nullable=True, unique=True),
    Column("title", Text, nullable=False),
    Column("data_class", String(32), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("length(title) BETWEEN 1 AND 120", name="ck_kb_document_title"),
    CheckConstraint(
        "data_class IN ('local_only', 'cloud_redacted', 'cloud_ok')", name="ck_kb_document_class"
    ),
    CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="ck_kb_document_hash"),
    Index("ix_kb_documents_class_id", "data_class", "id"),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), ForeignKey("deck_kb.documents.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("embedding", VectorType(), nullable=False),
    UniqueConstraint("document_id", "position", name="uq_kb_chunk_position"),
    CheckConstraint("position >= 0", name="ck_kb_chunk_position"),
    CheckConstraint("length(content) BETWEEN 1 AND 1500", name="ck_kb_chunk_content"),
    CheckConstraint(
        "content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex')",
        name="ck_kb_chunk_hash",
    ),
)
