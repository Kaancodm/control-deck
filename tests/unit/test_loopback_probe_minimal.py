"""The probe entrypoint runs the real adapter on the standard library alone."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts" / "loopback_probe_isolated.py"
REQUIRED = (
    "app/__init__.py",
    "app/assistant/__init__.py",
    "app/assistant/errors.py",
    "app/assistant/loopback_probe.py",
    "app/assistant/ollama_chat.py",
    "app/assistant/providers.py",
    "app/assistant/text.py",
    "app/core/__init__.py",
    "app/core/bind.py",
    "app/privacy/__init__.py",
    "app/privacy/normalization.py",
    "app/privacy/scanner.py",
    "app/workers/__init__.py",
    "app/workers/lanes/__init__.py",
    "app/workers/lanes/ollama_http.py",
    "app/workers/loopback_url.py",
)
FORBIDDEN = (
    "app/core/schemas.py",
    "app/workers/live_config.py",
    "app/assistant/web.py",
    "app/db/__init__.py",
)


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _chat_line(content: str) -> bytes:
    line = json.dumps(
        {
            "model": "fixture:localtest:local",
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
        }
    )
    return (line + "\n").encode()


def _serve() -> tuple[ThreadingHTTPServer, list[bytes]]:
    seen: list[bytes] = []

    class Handler(_Quiet):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            seen.append(raw)
            body = _chat_line("FIXTURE_REPLY_7")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, seen


def _run(env: dict[str, str], *, isolated: bool) -> subprocess.CompletedProcess[str]:
    command = [sys.executable]
    if isolated:
        command.append("-S")
    command.append(str(ENTRY))
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _base_env() -> dict[str, str]:
    path = os.environ.get("PATH", "")
    return {"PATH": path, "PYTHONDONTWRITEBYTECODE": "1"}


def test_isolated_probe_uses_the_same_adapter_without_third_party_modules() -> None:
    server, seen = _serve()
    try:
        port = server.server_address[1]
        env = _base_env()
        env["DECK_OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        env["DECK_ASSISTANT_OLLAMA_MODEL"] = "fixture:localtest"
        completed = _run(env, isolated=True)
    finally:
        server.shutdown()
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["runtime"] == "python-stdlib"
    assert report["third_party_modules"] == []
    assert report["prefix_ok"] is True
    assert report["uncensored_claim"] is False
    assert report["model_identity"] == "unverified"
    assert report["loopback_calls"] == 1 and report["network_calls"] == 0
    assert "FIXTURE_REPLY_7" in report["answer_preview"]
    implementation = report["implementation"]
    assert isinstance(implementation, dict)
    assert tuple(sorted(implementation)) == REQUIRED
    for relative in FORBIDDEN:
        assert relative not in implementation
    for relative, digest in implementation.items():
        hashed = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert digest == hashed
    assert seen
    body = json.loads(seen[0])
    assert body["model"] == "fixture:localtest:local"
    assert "LOOPBACK_PROBE_7" in body["messages"][0]["content"]
    assert "tools" not in body


def test_probe_without_isolated_flag_stops_before_a_socket() -> None:
    server, seen = _serve()
    try:
        port = server.server_address[1]
        env = _base_env()
        env["DECK_OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        env["DECK_ASSISTANT_OLLAMA_MODEL"] = "fixture:localtest"
        completed = _run(env, isolated=False)
    finally:
        server.shutdown()
    assert completed.returncode == 4
    assert "python -S required" in completed.stderr
    assert seen == []


def test_isolated_probe_rejects_a_remote_url_before_connect() -> None:
    env = _base_env()
    env["DECK_OLLAMA_URL"] = "http://example.com:11434"
    env["DECK_ASSISTANT_OLLAMA_MODEL"] = "fixture:localtest"
    completed = _run(env, isolated=True)
    assert completed.returncode == 3
    assert completed.stderr.strip() == "assistant_model_unconfigured"
