"""Detached local chat: bounded text in, optional validated draft out, no action capabilities."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from app.core.schemas import TaskV1
from app.privacy.scanner import FindingKind, scan_text
from app.ui.forms import validate_draft
from app.workers.process import run_process


class ChatError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Local chat is unavailable or its response is invalid.")


@dataclass(frozen=True)
class ChatRequest:
    prompt: str = field(repr=False)
    context: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        values = (self.prompt, *self.context)
        if (
            not self.prompt.strip()
            or len(self.context) > 4
            or sum(len(value.encode("utf-8")) for value in values) > 32768
        ):
            raise ChatError("chat_input_limit")
        if any(
            f.kind == FindingKind.SECRET
            for value in values
            for f in scan_text(value, source="chat")
        ):
            raise ChatError("chat_input_rejected")


@dataclass(frozen=True)
class ChatReply:
    answer: str = field(repr=False)
    draft: TaskV1 | None = field(default=None, repr=False)
    simulation: bool = False


class LocalChatAdapter(Protocol):
    lane: str

    def reply(self, request: ChatRequest) -> ChatReply: ...


class UnavailableChat:
    lane = "ollama"

    def reply(self, request: ChatRequest) -> ChatReply:
        raise ChatError("live_profile_unavailable")


class SimulatedChat:
    """Exercise the local subprocess boundary with a bundled fake CLI, never a model."""

    lane = "ollama"

    def reply(self, request: ChatRequest) -> ChatReply:
        script = Path(__file__).with_name("_fake_chat_cli.py")
        payload = json.dumps({"prompt": request.prompt, "context": request.context}).encode()
        with TemporaryDirectory(prefix="deck-chat-simulation-") as directory:
            result = run_process(
                [sys.executable, str(script), "run", "fake-local-chat"],
                input_bytes=payload,
                cwd=Path(directory),
                env={},
                timeout_s=10,
                cancel_requested=lambda: False,
                max_stdout_bytes=131072,
                max_stderr_bytes=1024,
            )
        if result.status != "completed" or result.returncode != 0:
            raise ChatError("chat_process_failed")
        return parse_reply(result.stdout, simulation=True)


def parse_reply(raw: bytes, *, simulation: bool = False) -> ChatReply:
    try:
        if len(raw) > 131072:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) - {"answer", "draft"}:
            raise ValueError
        answer = value["answer"]
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 65536:
            raise ValueError
        if any(f.kind == FindingKind.SECRET for f in scan_text(answer, source="chat_reply")):
            raise ValueError
        draft = validate_draft(json.dumps(value["draft"])) if value.get("draft") else None
        return ChatReply(answer=answer, draft=draft, simulation=simulation)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ChatError("chat_response_invalid") from None


def ask_local(adapter: LocalChatAdapter, request: ChatRequest) -> ChatReply:
    if adapter.lane != "ollama":
        raise ChatError("chat_lane_forbidden")
    reply = adapter.reply(request)
    # Injected adapters must pass the same boundary as subprocess responses.
    return parse_reply(
        json.dumps(
            {
                "answer": reply.answer,
                "draft": reply.draft.model_dump(mode="json") if reply.draft else None,
            }
        ).encode(),
        simulation=reply.simulation,
    )
