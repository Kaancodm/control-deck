"""Model and search ports.

``activate=True`` cannot open a socket. The dialogue loopback and the web
transport read their own opt-in variables and stay on the mock or the
unconfigured provider until those variables are set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.assistant.errors import AssistantError
from app.assistant.text import require_data_class

_COMMANDS = (
    "setze die datenklasse",
    "set data_class",
    "selbst die freigabe",
    "freigabe erteilen",
    "approve",
    "loesche",
    "lösche",
    "shell",
    "sudo",
    "merge",
    "merke dir im profil",
    "schreibe ins profil",
    "externe suche",
)


@dataclass(frozen=True)
class ProviderContract:
    """Offline description of a provider. ``activation`` stays off in this build."""

    provider_id: str
    activation: str
    data_classes: tuple[str, ...]
    opens_network: bool
    uncensored_claim: bool
    summary: str


@dataclass(frozen=True)
class ModelReply:
    """A labelled reply. ``profile`` and ``uncensored_claim`` are never a live attestation."""

    text: str
    profile: str
    data_class: str
    profile_written: bool = False
    live: bool = False
    network_calls: int = 0
    uncensored_claim: bool = False
    loopback_calls: int = 0
    model_identity: str = "not_applicable"


@dataclass(frozen=True)
class FetchedDocument:
    """One fetched page. ``url_label`` is a host or a fixed refusal, never a raw query."""

    title: str
    published_on: str
    text: str
    url_label: str
    claim: str = ""
    value: str = ""
    truncated: bool = False
    kind: str = "fetch"


class ModelAdapter(Protocol):
    profile: str

    def complete(self, prompt: str, *, data_class: str) -> ModelReply: ...


class SearchAdapter(Protocol):
    @property
    def network_calls(self) -> int: ...

    def search(self, query: str, *, data_class: str) -> tuple[FetchedDocument, ...]: ...

    def fetch(self, urls: Sequence[str], *, data_class: str) -> tuple[FetchedDocument, ...]: ...


_OLLAMA_LOCAL = ProviderContract(
    provider_id="ollama_local",
    activation="off",
    data_classes=("local_only",),
    opens_network=False,
    uncensored_claim=False,
    summary=(
        "Opt-in loopback chat for an owner-configured model tag. "
        "The disabled stand-in does not call the lane. "
        "A successful call does not attest an installed or uncensored model."
    ),
)
_WEB_BOUNDED = ProviderContract(
    provider_id="web_bounded",
    activation="off",
    data_classes=("cloud_ok",),
    opens_network=False,
    uncensored_claim=False,
    summary=(
        "Opt-in HTTPS fetch for an owner allowlist. "
        "local_only and cloud_redacted are refused with no request. "
        "Search is MediaWiki encyclopedia or a configured SearXNG instance."
    ),
)
_MOCK = ProviderContract(
    provider_id="mock",
    activation="off",
    data_classes=("local_only", "cloud_redacted", "cloud_ok"),
    opens_network=False,
    uncensored_claim=False,
    summary="Canned dialogue. Not a live or uncensored model.",
)


class LiveModelHook:
    """Real provider slot. Calling it is a hard error and performs no request."""

    profile = "disabled"
    contract = _OLLAMA_LOCAL

    def complete(self, prompt: str, *, data_class: str) -> ModelReply:
        del prompt
        require_data_class(data_class)
        raise AssistantError("live_model_disabled")


class DisabledOllamaLocal:
    """Typed stand-in for the existing local Ollama lane. Live activation is ignored."""

    profile = "disabled"
    contract = _OLLAMA_LOCAL

    def complete(self, prompt: str, *, data_class: str) -> ModelReply:
        del prompt
        checked = require_data_class(data_class)
        if checked != "local_only":
            raise AssistantError("assistant_data_class")
        raise AssistantError("live_model_disabled")


class MockModel:
    """Local canned replies. This is not an uncensored or live model."""

    profile = "mock"
    contract = _MOCK

    def complete(self, prompt: str, *, data_class: str) -> ModelReply:
        checked = require_data_class(data_class)
        lowered = prompt.casefold()
        if any(marker in lowered for marker in _COMMANDS):
            text = (
                "Mock, kein Live-Modell. Das kann ich nicht tun. "
                "Dialog hat keine Freigabe, kein Profilschreiben, keine Shell und keinen Netzabruf."
            )
        else:
            clip = " ".join(prompt.split())[:80]
            text = (
                "Mock, kein Live-Modell. Dein Satz bleibt Dialog und legt keinen Auftrag an. "
                "Eine Karte, ein Preset oder die Suche als Auftrag geht in die bestehende "
                "Start-Freigabe. Ich schreibe dein Profil nicht und aendere die Datenklasse nicht. "
                f"Aufgenommen: {clip}"
            )
        return ModelReply(
            text=text,
            profile=self.profile,
            data_class=checked,
            profile_written=False,
            live=False,
            network_calls=0,
            uncensored_claim=False,
        )


class DisabledWebSearch:
    """Bounded web slot. ``local_only`` is refused before a refusal counter or a socket."""

    contract = _WEB_BOUNDED

    def __init__(self) -> None:
        self.refusals = 0
        self.network_calls = 0

    def search(self, query: str, *, data_class: str) -> tuple[FetchedDocument, ...]:
        del query
        self._refuse(data_class)
        return ()

    def fetch(self, urls: Sequence[str], *, data_class: str) -> tuple[FetchedDocument, ...]:
        del urls
        self._refuse(data_class)
        return ()

    def _refuse(self, data_class: str) -> None:
        checked = require_data_class(data_class)
        if checked == "local_only":
            raise AssistantError("assistant_data_class")
        self.refusals += 1
        raise AssistantError("live_search_disabled")


def resolve_model() -> ModelAdapter:
    """Return the mock unless the assistant loopback flag and its settings are present."""
    if os.environ.get("DECK_ASSISTANT_OLLAMA") == "1":
        from app.assistant.ollama_chat import OllamaDialogueAdapter

        return OllamaDialogueAdapter.from_env()
    return MockModel()


def resolve_provider(provider_id: str, *, activate: bool = False) -> ModelAdapter:
    """Return a port. ``activate`` is accepted and cannot open a live provider."""
    del activate
    if provider_id == "mock":
        return MockModel()
    if provider_id == "ollama_local":
        return DisabledOllamaLocal()
    raise AssistantError("assistant_input")


def resolve_search() -> SearchAdapter:
    """Return the unconfigured web provider, or the allowlisted transport when configured."""
    from app.assistant.web import load_search

    return load_search(os.environ)


def activation_status(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Name missing switches without printing their values."""
    env = os.environ if environment is None else environment
    lines: list[str] = []
    if env.get("DECK_ASSISTANT_OLLAMA") == "1":
        missing = [
            name
            for name in ("DECK_OLLAMA_URL", "DECK_ASSISTANT_OLLAMA_MODEL")
            if not env.get(name, "").strip()
        ]
        if missing:
            lines.append("Loopback unvollständig: " + ", ".join(missing))
        else:
            lines.append(
                "Loopback ist eingeschaltet. Die Identität bleibt unbelegt, "
                "kein Uncensored-Nachweis."
            )
    else:
        lines.append(
            "Modellstandard ist der Mock, kein Live-Modell. DECK_ASSISTANT_OLLAMA ist aus."
        )
    if env.get("DECK_ASSISTANT_WEB") == "1":
        provider = env.get("DECK_ASSISTANT_SEARCH_PROVIDER", "").strip()
        missing_web: list[str] = []
        if not env.get("DECK_ASSISTANT_WEB_ALLOWLIST", "").strip():
            missing_web.append("DECK_ASSISTANT_WEB_ALLOWLIST")
        if provider not in {"mediawiki", "searxng"}:
            missing_web.append("DECK_ASSISTANT_SEARCH_PROVIDER")
        elif provider == "mediawiki" and not env.get("DECK_ASSISTANT_MEDIAWIKI_API", "").strip():
            missing_web.append("DECK_ASSISTANT_MEDIAWIKI_API")
        elif provider == "searxng" and not env.get("DECK_ASSISTANT_SEARXNG_URL", "").strip():
            missing_web.append("DECK_ASSISTANT_SEARXNG_URL")
        if missing_web:
            lines.append("Web unvollständig: " + ", ".join(missing_web))
        elif provider == "mediawiki":
            lines.append(
                "Enzyklopädie-Suche über die MediaWiki-API ist konfiguriert. "
                "Das ist keine allgemeine Websuche."
            )
        else:
            lines.append("SearXNG ist konfiguriert. Eine öffentliche Instanz wird nicht gestartet.")
    else:
        lines.append(
            "Webabruf ist aus. Nötig sind DECK_ASSISTANT_WEB, "
            "DECK_ASSISTANT_WEB_ALLOWLIST und DECK_ASSISTANT_SEARCH_PROVIDER."
        )
    return tuple(lines)
