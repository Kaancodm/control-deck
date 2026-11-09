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
    "app/workers/lanes/ollama_keep_alive.py",
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
    assert "prefix_ok" not in report
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


def test_isolated_probe_reports_a_missing_model_without_the_body() -> None:
    class Handler(_Quiet):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"error":"missing sk-benchmarktrap1"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        env = _base_env()
        env["DECK_OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        env["DECK_ASSISTANT_OLLAMA_MODEL"] = "fixture:localtest"
        completed = _run(env, isolated=True)
    finally:
        server.shutdown()
    assert completed.returncode == 3
    report = json.loads(completed.stderr)
    assert report["code"] == "assistant_model_missing"
    assert report["detail"]["class"] == "model_missing"
    assert report["detail"]["http_status"] == 404
    assert report["third_party_modules"] == []
    assert "app/workers/lanes/ollama_http.py" in report["implementation"]
    assert "sk-benchmarktrap1" not in completed.stderr
    assert completed.stdout == ""


def test_isolated_probe_sends_the_installed_namespaced_tag_in_json() -> None:
    tag = "huihui_ai/qwen3-abliterated:4b-v2"
    seen: list[tuple[str, bytes]] = []

    class Handler(_Quiet):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            seen.append((self.path, raw))
            requested = json.loads(raw)["model"]
            line = json.dumps(
                {
                    "model": requested,
                    "message": {"role": "assistant", "content": "FIXTURE_REPLY_7"},
                    "done": True,
                    "done_reason": "stop",
                }
            )
            body = (line + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        env = _base_env()
        env["DECK_OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        env["DECK_ASSISTANT_OLLAMA_MODEL"] = tag
        completed = _run(env, isolated=True)
    finally:
        server.shutdown()
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["configured_model"] == tag
    assert report["third_party_modules"] == []
    assert report["uncensored_claim"] is False
    assert len(seen) == 1
    path, raw = seen[0]
    assert path == "/api/chat"
    assert tag not in path
    assert json.loads(raw)["model"] == f"{tag}:local"


def test_isolated_probe_rejects_a_path_model_before_connect() -> None:
    seen: list[bytes] = []

    class Handler(_Quiet):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            seen.append(self.rfile.read(length))
            self.send_response(500)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        env = _base_env()
        env["DECK_OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        env["DECK_ASSISTANT_OLLAMA_MODEL"] = "huihui_ai/../../x:tag"
        completed = _run(env, isolated=True)
    finally:
        server.shutdown()
    assert completed.returncode == 3
    report = json.loads(completed.stderr)
    assert report["ok"] is False
    assert report["code"] == "assistant_model_unconfigured"
    assert report["detail"] == {}
    assert "huihui" not in completed.stderr
    assert seen == []


def test_isolated_probe_rejects_a_remote_url_before_connect() -> None:
    env = _base_env()
    env["DECK_OLLAMA_URL"] = "http://example.com:11434"
    env["DECK_ASSISTANT_OLLAMA_MODEL"] = "fixture:localtest"
    completed = _run(env, isolated=True)
    assert completed.returncode == 3
    report = json.loads(completed.stderr)
    assert report["ok"] is False
    assert report["code"] == "assistant_model_unconfigured"
    assert report["third_party_modules"] == []
    assert "app/assistant/ollama_chat.py" in report["implementation"]
    assert "example.com" not in completed.stderr
