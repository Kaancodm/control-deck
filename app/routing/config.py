"""Strict, bounded local routing configuration without provider connections."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Hashable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node, ScalarNode

from app.core.schemas import DataClass, TaskKind, Worker

_DEFAULT_FILE = Path(__file__).resolve().parents[2] / "config" / "router.yaml"
_MAX_CONFIG_BYTES = 131072
_MAX_CONFIG_NODES = 4096
_MAX_CONFIG_DEPTH = 32
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


class RouterConfigurationError(RuntimeError):
    """A failure that does not reveal paths or configuration values."""

    def __init__(self) -> None:
        super().__init__("Routing configuration is unavailable or invalid.")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LaneConfig(_FrozenModel):
    enabled: StrictBool = True
    daily_attempt_limit: StrictInt | None = Field(default=None, ge=0)
    models: dict[str, StrictStr] = Field(default_factory=dict)

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: dict[str, str]) -> dict[str, str]:
        valid_keys = {"default", *(kind.value for kind in TaskKind)}
        if any(
            key not in valid_keys or not _MODEL_NAME.fullmatch(model)
            for key, model in value.items()
        ):
            raise ValueError("Models require known task kinds and bounded model identifiers")
        return value


class RoutingRule(_FrozenModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kinds: list[TaskKind] = Field(default_factory=list, max_length=len(TaskKind))
    data_classes: list[DataClass] = Field(default_factory=list, max_length=len(DataClass))
    allowed_workers: list[Worker] | None = Field(default=None, max_length=len(Worker))

    @model_validator(mode="after")
    def reject_duplicate_members(self) -> RoutingRule:
        for entries in (self.kinds, self.data_classes, self.allowed_workers):
            if entries is not None and len(entries) != len(set(entries)):
                raise ValueError("Routing rule members must be unique")
        return self


def _default_lanes() -> dict[Worker, LaneConfig]:
    return {worker: LaneConfig() for worker in Worker}


class RouterConfig(_FrozenModel):
    version: Literal[1] = 1
    lanes: dict[Worker, LaneConfig] = Field(default_factory=_default_lanes)
    rules: list[RoutingRule] = Field(default_factory=list, max_length=128)

    @field_validator("version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Routing version must be an integer")
        return value

    @field_validator("lanes")
    @classmethod
    def fill_missing_lanes(cls, value: dict[Worker, LaneConfig]) -> dict[Worker, LaneConfig]:
        return {**_default_lanes(), **value}

    @field_validator("rules")
    @classmethod
    def reject_duplicate_ids(cls, value: list[RoutingRule]) -> list[RoutingRule]:
        if len({rule.id for rule in value}) != len(value):
            raise ValueError("Routing rule identifiers must be unique")
        return value


class _RouterLoader(yaml.SafeLoader):
    """Reject aliases, duplicate keys and excessive nesting before construction."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._node_count = 0
        self._depth = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node | None:
        if self.check_event(AliasEvent):
            raise ValueError("YAML aliases are not supported")
        self._node_count += 1
        self._depth += 1
        try:
            if self._node_count > _MAX_CONFIG_NODES or self._depth > _MAX_CONFIG_DEPTH:
                raise ValueError("Routing configuration is too complex")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        keys: set[str] = set()
        for key_node, _ in node.value:
            if (
                not isinstance(key_node, ScalarNode)
                or key_node.tag != "tag:yaml.org,2002:str"
                or key_node.value in keys
            ):
                raise ValueError("Routing mappings require unique string keys")
            keys.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def load_router_config(path: Path) -> RouterConfig:
    """Read one regular UTF-8 YAML file, bounded before parsing and validated strictly."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_CONFIG_BYTES:
                raise ValueError("Invalid configuration file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(_MAX_CONFIG_BYTES + 1)
            after = os.fstat(descriptor)
            if (
                len(data) > _MAX_CONFIG_BYTES
                or len(data) != before.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError("Configuration changed while reading")
        finally:
            os.close(descriptor)
        loaded = yaml.load(data.decode("utf-8"), Loader=_RouterLoader)
        if not isinstance(loaded, dict):
            raise ValueError("Routing configuration must be a mapping")
        return RouterConfig.model_validate(loaded)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        raise RouterConfigurationError() from None


def get_router_config() -> RouterConfig:
    """Reload explicit local configuration at each boundary; never fall back after error."""
    configured = os.environ.get("DECK_ROUTER_FILE")
    if configured is not None and not configured.strip():
        raise RouterConfigurationError()
    return load_router_config(Path(configured) if configured is not None else _DEFAULT_FILE)
