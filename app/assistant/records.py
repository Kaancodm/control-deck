"""Owner-controlled assistant rows. These are not a second task queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssistantProfileVersion(Base):
    """Append-only owner profile notes. The visible note is the highest version."""

    __tablename__ = "assistant_profile_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantCard(Base):
    """Current command card. Older text lives in ``assistant_card_versions``."""

    __tablename__ = "assistant_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    placeholders: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantCardVersion(Base):
    """Immutable card snapshot. Edits insert a row; nothing here is deleted."""

    __tablename__ = "assistant_card_versions"

    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_cards.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    placeholders: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_assistant_card_versions_card_id", "card_id"),)


class AssistantTurn(Base):
    """Optional dialogue row. Inserted only when the owner asks to keep it."""

    __tablename__ = "assistant_turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_class: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_text: Mapped[str] = mapped_column(Text, nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    reply_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_assistant_turns_created_at", "created_at"),)


class AssistantOrderKey(Base):
    """Idempotency key for an order that already entered the task table."""

    __tablename__ = "assistant_order_keys"

    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def placeholders_of(value: Any) -> list[str]:
    """Return stored placeholder names, ignoring a malformed JSON value."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return list(value)
