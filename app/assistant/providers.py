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
class ModelReply:
    """A labelled mock reply. ``profile`` is never a live attestation."""

    text: str
    profile: str
    data_class: str
    profile_written: bool = False


class ModelAdapter(Protocol):
    profile: str

    def complete(self, prompt: str, *, data_class: str) -> ModelReply: ...


class SearchAdapter(Protocol):
    def search(self, query: str, *, data_class: str) -> None: ...


class LiveModelHook:
    """Real provider slot. Calling it is a hard error and performs no request."""

    profile = "disabled"

    def complete(self, prompt: str, *, data_class: str) -> ModelReply:
        require_data_class(data_class)
        raise AssistantError("live_model_disabled")


class MockModel:
    """Local canned replies. This is not an uncensored or live model."""

    profile = "mock"

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
            text=text, profile=self.profile, data_class=checked, profile_written=False
        )


class DisabledWebSearch:
    """Live search slot. A call is counted as a refusal and opens no connection."""

    def __init__(self) -> None:
        self.refusals = 0
        self.network_calls = 0

    def search(self, query: str, *, data_class: str) -> None:
        require_data_class(data_class)
        self.refusals += 1
        raise AssistantError("live_search_disabled")


def resolve_model() -> ModelAdapter:
    """Return the mock. No environment switch wires the live hook."""
    return MockModel()


def resolve_search() -> DisabledWebSearch:
    """Return the disabled web hook. There is no live search provider."""
    return DisabledWebSearch()
