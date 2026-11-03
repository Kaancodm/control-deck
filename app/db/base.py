"""SQLAlchemy declarative base and shared metadata."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root declarative base for Control Deck ORM models."""
