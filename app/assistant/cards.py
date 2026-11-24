"""Versioned command cards. Templates are data and are never executed."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.errors import AssistantError
from app.assistant.records import AssistantCard, AssistantCardVersion, placeholders_of
from app.assistant.text import (
    NAME_MAX,
    TEMPLATE_MAX,
    VALUE_MAX,
    bounded,
    reject_secrets,
    require_data_class,
)
from app.core.schema_loader import load_schema

_PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]{0,31})\}\}")
_CARD_VALIDATOR = Draft202012Validator(load_schema("assistant_card.v1.json"))

PRESET_IDS = {
    "coding": uuid.UUID("8d5c1a2e-1111-4111-8111-111111111101"),
    "checklist": uuid.UUID("8d5c1a2e-1111-4111-8111-111111111102"),
    "planning": uuid.UUID("8d5c1a2e-1111-4111-8111-111111111103"),
}


class AssistantCardV1(BaseModel):
    """Pydantic mirror of ``assistant_card.v1``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["assistant_card.v1"]
    name: str = Field(min_length=1, max_length=NAME_MAX)
    category: Literal["frage", "code", "checkliste", "planung"]
    template: str = Field(min_length=1, max_length=TEMPLATE_MAX)
    placeholders: list[str] = Field(max_length=8)
    data_class: Literal["local_only", "cloud_redacted", "cloud_ok"]

    @model_validator(mode="after")
    def placeholders_match_template(self) -> AssistantCardV1:
        found = _PLACEHOLDER.findall(self.template)
        if len(found) != len(set(found)) or set(found) != set(self.placeholders):
            raise ValueError("placeholders must match the template exactly")
        if _PLACEHOLDER.sub("", self.template).strip() == "" and not self.template.strip():
            raise ValueError("template is empty")
        return self


@dataclass(frozen=True)
class Preset:
    id: uuid.UUID
    name: str
    category: Literal["frage", "code", "checkliste", "planung"]
    template: str
    placeholders: tuple[str, ...]
    data_class: str


PRESETS = (
    Preset(
        id=PRESET_IDS["coding"],
        name="Coding-Hilfe",
        category="code",
        template=(
            "Erklaere den lokalen Aenderungswunsch {{thema}} als Entwurf. "
            "Keine Freigabe und kein Netz."
        ),
        placeholders=("thema",),
        data_class="local_only",
    ),
    Preset(
        id=PRESET_IDS["checklist"],
        name="Checkliste",
        category="checkliste",
        template=(
            "Erstelle eine Checkliste fuer {{thema}}. "
            "Offene Punkte bleiben offen und werden nicht als erledigt markiert."
        ),
        placeholders=("thema",),
        data_class="local_only",
    ),
    Preset(
        id=PRESET_IDS["planning"],
        name="Planung",
        category="planung",
        template=(
            "Ordne {{thema}} in nachvollziehbare Schritte. "
            "Erfinde keine Termine und rufe nichts extern ab."
        ),
        placeholders=("thema",),
        data_class="local_only",
    ),
)


def validate_card(payload: Mapping[str, Any]) -> AssistantCardV1:
    """Reject unknown fields, secrets, and placeholder drift."""
    if list(_CARD_VALIDATOR.iter_errors(dict(payload))):
        raise AssistantError("assistant_card")
    try:
        card = AssistantCardV1.model_validate(payload)
    except ValidationError as exc:
        raise AssistantError("assistant_card") from exc
    reject_secrets(card.template, source="card_template")
    reject_secrets(card.name, source="card_name")
    return card


def parse_card_json(raw: str) -> AssistantCardV1:
    """Import one card document. Oversize and secrets never reach the table."""
    if not isinstance(raw, str) or len(raw) > 20_000:
        raise AssistantError("assistant_oversize")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssistantError("assistant_card") from exc
    if not isinstance(payload, dict):
        raise AssistantError("assistant_card")
    return validate_card(payload)


def substitute(template: str, values: Mapping[str, str], placeholders: Sequence[str]) -> str:
    """Replace ``{{name}}`` with owner text. The result is still data, not a command."""
    required = tuple(placeholders)
    if any(name not in values for name in required):
        raise AssistantError("assistant_card")
    cleaned: dict[str, str] = {}
    for name in required:
        value = values[name]
        if not isinstance(value, str) or len(value) > VALUE_MAX or "{{" in value or "}}" in value:
            raise AssistantError("assistant_card")
        reject_secrets(value, source="card_value")
        cleaned[name] = value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in cleaned:
            raise AssistantError("assistant_card")
        return cleaned[name]

    rendered = _PLACEHOLDER.sub(replace, template)
    if _PLACEHOLDER.search(rendered):
        raise AssistantError("assistant_card")
    return rendered


def card_document(card: AssistantCard) -> dict[str, Any]:
    """Export the current version as ``assistant_card.v1``."""
    payload = {
        "schema_version": "assistant_card.v1",
        "name": card.name,
        "category": card.category,
        "template": card.template,
        "placeholders": placeholders_of(card.placeholders),
        "data_class": card.data_class,
    }
    validate_card(payload)
    return payload


def list_cards(session: Session) -> list[AssistantCard]:
    return list(
        session.scalars(select(AssistantCard).order_by(AssistantCard.name, AssistantCard.id))
    )


def ensure_presets(session: Session) -> None:
    """Insert the three question presets once. Later owner edits stay in place."""
    for preset in PRESETS:
        if session.get(AssistantCard, preset.id) is None:
            _insert_card(
                session,
                card_id=preset.id,
                name=preset.name,
                category=preset.category,
                template=preset.template,
                placeholders=list(preset.placeholders),
                data_class=preset.data_class,
            )


def create_card(session: Session, card: AssistantCardV1) -> AssistantCard:
    return _insert_card(
        session,
        card_id=uuid.uuid4(),
        name=card.name,
        category=card.category,
        template=card.template,
        placeholders=card.placeholders,
        data_class=card.data_class,
    )


def copy_card(session: Session, card_id: uuid.UUID) -> AssistantCard:
    source = _require_card(session, card_id)
    name = f"{source.name} Kopie"
    if len(name) > NAME_MAX:
        name = name[:NAME_MAX].rstrip()
    return _insert_card(
        session,
        card_id=uuid.uuid4(),
        name=name,
        category=source.category,
        template=source.template,
        placeholders=placeholders_of(source.placeholders),
        data_class=source.data_class,
    )


def update_card(session: Session, card_id: uuid.UUID, card: AssistantCardV1) -> AssistantCard:
    """Store a new version and keep every previous snapshot."""
    current = _require_card(session, card_id)
    current.version += 1
    current.name = card.name
    current.category = card.category
    current.template = card.template
    current.placeholders = card.placeholders
    current.data_class = card.data_class
    current.updated_at = datetime.now(UTC)
    _insert_version(session, current)
    session.flush()
    return current


def versions_for(session: Session, card_id: uuid.UUID) -> list[AssistantCardVersion]:
    return list(
        session.scalars(
            select(AssistantCardVersion)
            .where(AssistantCardVersion.card_id == card_id)
            .order_by(AssistantCardVersion.version)
        )
    )


def _require_card(session: Session, card_id: uuid.UUID) -> AssistantCard:
    card = session.get(AssistantCard, card_id)
    if card is None:
        raise AssistantError("assistant_card")
    return card


def _insert_card(
    session: Session,
    *,
    card_id: uuid.UUID,
    name: str,
    category: str,
    template: str,
    placeholders: list[str],
    data_class: str,
) -> AssistantCard:
    checked = validate_card(
        {
            "schema_version": "assistant_card.v1",
            "name": name,
            "category": category,
            "template": template,
            "placeholders": placeholders,
            "data_class": require_data_class(data_class),
        }
    )
    row = AssistantCard(
        id=card_id,
        name=checked.name,
        category=checked.category,
        version=1,
        template=checked.template,
        placeholders=checked.placeholders,
        data_class=checked.data_class,
    )
    session.add(row)
    session.flush()
    _insert_version(session, row)
    session.flush()
    return row


def _insert_version(session: Session, card: AssistantCard) -> None:
    session.add(
        AssistantCardVersion(
            card_id=card.id,
            version=card.version,
            name=card.name,
            category=card.category,
            template=card.template,
            placeholders=placeholders_of(card.placeholders),
            data_class=card.data_class,
        )
    )


def card_from_form(
    *,
    name: str,
    category: str,
    template: str,
    data_class: str,
    placeholders: str,
) -> AssistantCardV1:
    names = [item for item in placeholders.split() if item]
    return validate_card(
        {
            "schema_version": "assistant_card.v1",
            "name": bounded(name, limit=NAME_MAX, code="assistant_card"),
            "category": category,
            "template": template,
            "placeholders": names,
            "data_class": data_class,
        }
    )
