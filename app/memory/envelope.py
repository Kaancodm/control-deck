"""``channel_turn.v1``: the one normalized envelope every channel adapter produces."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.schema_loader import load_schema
from app.memory.policy import ConversationError

SCHEMA_VERSION = "channel_turn.v1"
TEXT_MAX = 16000
# Deck replies use this prefix. Inbound turns must not mint it.
RESERVED_MESSAGE_PREFIX = "deck-reply:"
# A single colon is allowed (source clocks, slack timestamps). A scheme separator is not.
# The JSON Schema also bans both with look-ahead; pydantic-core regexes have no look-around,
# so the Pydantic mirror uses the plain character class and _external_ids_allowed() checks.
_EXTERNAL_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/#+-]{0,199}$"
_MESSAGE_ID = _EXTERNAL_ID
_SOURCE_REF = (
    r"^(deck_ui|slack|n8n|grok|claude|claude_code|file|backfill):"
    r"[A-Za-z0-9][A-Za-z0-9._/#+-]{0,279}$"
)
_OCCURRED_AT = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_VALIDATOR = Draft202012Validator(load_schema("channel_turn.v1.json"))


class SourceRef(BaseModel):
    """Filename or link identifier plus optional content hash. Never a hostname."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: str = Field(min_length=1, max_length=300, pattern=_SOURCE_REF)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ChannelTurnV1(BaseModel):
    """Pydantic mirror of ``schemas/channel_turn.v1.json``. ``occurred_at`` is untrusted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["channel_turn.v1"]
    channel: Literal["deck_ui", "slack", "n8n", "grok", "claude", "claude_code"]
    account: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    external_thread_id: str = Field(min_length=1, max_length=200, pattern=_EXTERNAL_ID)
    external_message_id: str | None = Field(min_length=1, max_length=200, pattern=_MESSAGE_ID)
    role: Literal["owner", "assistant"]
    text: str = Field(min_length=1, max_length=TEXT_MAX, repr=False)
    occurred_at: str = Field(pattern=_OCCURRED_AT)
    data_class: Literal["local_only", "cloud_redacted", "cloud_ok"]
    ingest_method: Literal["deck_ui", "live_push", "import_file", "owner_typed"]
    source_ref: SourceRef
    in_reply_to: str | None = Field(
        default=None, min_length=1, max_length=200, pattern=_EXTERNAL_ID
    )

    @field_validator("external_thread_id", "external_message_id", "in_reply_to")
    @classmethod
    def _no_scheme(cls, value: str | None) -> str | None:
        if value is not None and "://" in value:
            raise ValueError("scheme separator")
        return value

    @field_validator("external_message_id")
    @classmethod
    def _not_reserved(cls, value: str | None) -> str | None:
        if value is not None and value.startswith(RESERVED_MESSAGE_PREFIX):
            raise ValueError("reserved prefix")
        return value

    def occurred_at_value(self) -> datetime:
        """Parse the source clock. It is provenance only, never an ordering authority."""
        try:
            parsed = datetime.fromisoformat(self.occurred_at)
        except ValueError:
            raise ConversationError("invalid_envelope") from None
        if parsed.tzinfo is None:
            raise ConversationError("invalid_envelope")
        return parsed


def json_schema_valid(payload: Mapping[str, Any]) -> bool:
    return _VALIDATOR.is_valid(dict(payload))


def parse_turn(payload: Mapping[str, Any] | ChannelTurnV1) -> ChannelTurnV1:
    """Validate against the JSON Schema and the Pydantic mirror; both must accept."""
    if isinstance(payload, ChannelTurnV1):
        payload = payload.model_dump(mode="json", exclude_unset=True)
    if not isinstance(payload, Mapping) or not json_schema_valid(payload):
        raise ConversationError("invalid_envelope")
    try:
        turn = ChannelTurnV1.model_validate(dict(payload))
    except ValidationError:
        raise ConversationError("invalid_envelope") from None
    if not _external_ids_allowed(turn):
        raise ConversationError("invalid_envelope")
    turn.occurred_at_value()
    return turn


def _external_ids_allowed(turn: ChannelTurnV1) -> bool:
    """Reject scheme separators and the Deck reply id prefix on inbound turns."""
    for value in (turn.external_thread_id, turn.external_message_id, turn.in_reply_to):
        if value is not None and "://" in value:
            return False
    message_id = turn.external_message_id
    return message_id is None or not message_id.startswith(RESERVED_MESSAGE_PREFIX)
