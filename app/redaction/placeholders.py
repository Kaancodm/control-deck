"""Canonical placeholders with an explicit task namespace."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RedactionKind(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IBAN = "IBAN"
    AHV = "AHV"
    IP = "IP"
    TERM = "TERM"


MAX_PLACEHOLDER_INDEX = 2147483647
PLACEHOLDER_PATTERN = re.compile(
    r"\[\[CD_([a-f0-9]{32})_(EMAIL|PHONE|IBAN|AHV|IP|TERM)_([1-9][0-9]{0,9})\]\]"
)
RESERVED_PREFIX = re.compile(r"\[\[CD", re.IGNORECASE)


@dataclass(frozen=True)
class Placeholder:
    task_id: UUID
    kind: RedactionKind
    index: int


def parse_placeholder(value: str) -> Placeholder:
    """Validate a complete token without including it in an error message."""
    match = PLACEHOLDER_PATTERN.fullmatch(value)
    if match is None or int(match[3]) > MAX_PLACEHOLDER_INDEX:
        raise ValueError("Invalid redaction placeholder.")
    return Placeholder(UUID(hex=match[1]), RedactionKind(match[2]), int(match[3]))


def format_placeholder(task_id: UUID, kind: RedactionKind, index: int) -> str:
    if isinstance(index, bool) or not 1 <= index <= MAX_PLACEHOLDER_INDEX:
        raise ValueError("Invalid redaction placeholder index.")
    return f"[[CD_{task_id.hex}_{kind.value}_{index}]]"
