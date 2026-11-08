"""Model and search ports. The live hooks cannot be switched on from here."""

from __future__ import annotations

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


class ModelAdapter(Protocol):
    profile: str

    def complete(self, prompt: str, *, data_class: str) -> ModelReply: ...


class SearchAdapter(Protocol):
    def search(self, query: str, *, data_class: str) -> None: ...


_OLLAMA_LOCAL = ProviderContract(
    provider_id="ollama_local",
    activation="off",
    data_classes=("local_only",),
    opens_network=False,
    uncensored_claim=False,
    summary=(
        "Contract for the existing loopback Ollama lane. "
        "This object does not call that lane and does not attest any installed model."
    ),
)
_WEB_BOUNDED = ProviderContract(
    provider_id="web_bounded",
    activation="off",
    data_classes=("cloud_redacted", "cloud_ok"),
    opens_network=False,
    uncensored_claim=False,
    summary="Contract for a later bounded web provider. local_only is refused with no request.",
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

    def search(self, query: str, *, data_class: str) -> None:
        del query
        checked = require_data_class(data_class)
        if checked == "local_only":
            raise AssistantError("assistant_data_class")
        self.refusals += 1
        raise AssistantError("live_search_disabled")


def resolve_model() -> ModelAdapter:
    """Return the mock. No environment switch wires the live hook."""
    return resolve_provider("mock", activate=False)


def resolve_provider(provider_id: str, *, activate: bool = False) -> ModelAdapter:
    """Return a port. ``activate`` is accepted and cannot open a live provider."""
    del activate
    if provider_id == "mock":
        return MockModel()
    if provider_id == "ollama_local":
        return DisabledOllamaLocal()
    raise AssistantError("assistant_input")


def resolve_search() -> DisabledWebSearch:
    """Return the disabled web hook. There is no live search provider."""
    return DisabledWebSearch()
