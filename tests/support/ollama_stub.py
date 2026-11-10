"""Configurable standard-library Ollama HTTP fixture bound to ephemeral loopback."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def embedding_vector(text, dimension):
    """Deterministic non-zero synthetic vector; equal text gives an equal vector."""
    values = []
    counter = 0
    while len(values) < dimension:
        digest = hashlib.sha256(counter.to_bytes(4, "big") + text.encode()).digest()
        values.extend((byte + 1) / 256 for byte in digest)
        counter += 1
    return values[:dimension]


class OllamaStub:
    def __init__(
        self,
        *,
        models=("fixture:latest",),
        output="CANARY-OK",
        version="0.40.14",
        capabilities=("completion",),
        chat_chunks=None,
        chunk_delay_s=0,
        initial_delay_s=0,
        header_delay_s=0,
        status_by_path=None,
        status_sequence_by_path=None,
        retry_after=None,
        error_body=b'{"error":"synthetic provider error marker"}',
        raw_by_path=None,
        show_extra=None,
        show_by_model=None,
        embed_dimension=1024,
        embed_model=None,
        embed_count_delta=0,
        embed_extra=None,
    ):
        self.models = models
        self.output = output
        self.version = version
        self.capabilities = capabilities
        self.chat_chunks = chat_chunks
        self.chunk_delay_s = chunk_delay_s
        self.initial_delay_s = initial_delay_s
        self.header_delay_s = header_delay_s
        self.status_by_path = status_by_path or {}
        self.status_sequence_by_path = {
            path: list(codes) for path, codes in (status_sequence_by_path or {}).items()
        }
        self.retry_after = retry_after
        self.error_body = error_body
        self.raw_by_path = raw_by_path or {}
        self.show_extra = show_extra or {}
        self.show_by_model = show_by_model or {}
        # /api/embed: width per vector, echoed model (None echoes the request),
        # vectors more or fewer than inputs, and extra top-level response fields.
        self.embed_dimension = embed_dimension
        self.embed_model = embed_model
        self.embed_count_delta = embed_count_delta
        self.embed_extra = embed_extra or {}
        self.requests = []
        self.chat_started = threading.Event()
        self.disconnected = threading.Event()
        self._stop = threading.Event()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                try:
                    size = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(size) if size else b""
                    body = json.loads(raw) if raw else None
                    fixture.requests.append(
                        {
                            "method": self.command,
                            "path": self.path,
                            "body": body,
                            "headers": dict(self.headers),
                        }
                    )
                    if self.path == "/api/chat":
                        fixture.chat_started.set()
                    if fixture._stop.wait(fixture.header_delay_s):
                        return
                    sequence = fixture.status_sequence_by_path.get(self.path)
                    status = (
                        sequence.pop(0) if sequence else fixture.status_by_path.get(self.path, 200)
                    )
                    self.send_response(status)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Connection", "close")
                    if status == 503 and fixture.retry_after is not None:
                        self.send_header("Retry-After", fixture.retry_after)
                    self.end_headers()
                    self.close_connection = True
                    if status != 200:
                        self.wfile.write(fixture.error_body)
                    elif self.path in fixture.raw_by_path:
                        self.wfile.write(fixture.raw_by_path[self.path])
                    elif self.path == "/api/version":
                        self.write_json({"version": fixture.version})
                    elif self.path == "/api/tags":
                        self.write_json({"models": [{"name": name} for name in fixture.models]})
                    elif self.path == "/api/show":
                        self.write_json(
                            {
                                "capabilities": list(fixture.capabilities),
                                "model_info": {"fixture.context_length": 4096},
                                **fixture.show_extra,
                                **fixture.show_by_model.get(body["model"], {}),
                            }
                        )
                    elif self.path == "/api/embed":
                        inputs = body["input"]
                        count = max(0, len(inputs) + fixture.embed_count_delta)
                        texts = (inputs * (count // max(1, len(inputs)) + 1))[:count]
                        self.write_json(
                            {
                                "model": fixture.embed_model or body["model"],
                                "embeddings": [
                                    embedding_vector(text, fixture.embed_dimension)
                                    for text in texts
                                ],
                                "total_duration": 1000,
                                "load_duration": 0,
                                "prompt_eval_count": len(inputs),
                                **fixture.embed_extra,
                            }
                        )
                    elif self.path == "/api/chat":
                        if fixture._stop.wait(fixture.initial_delay_s):
                            return
                        model = body["model"]
                        content = (
                            body["messages"][0]["content"]
                            if fixture.output == "__echo__"
                            else fixture.output
                        )
                        chunks = fixture.chat_chunks
                        if chunks is None:
                            chunks = [
                                {
                                    "model": model,
                                    "message": {"role": "assistant", "content": content},
                                    "done": False,
                                },
                                {
                                    "model": model,
                                    "message": {"role": "assistant", "content": ""},
                                    "done": True,
                                    "done_reason": "stop",
                                    "prompt_eval_count": 3,
                                    "eval_count": 4,
                                },
                            ]
                        for index, chunk in enumerate(chunks):
                            if index and fixture._stop.wait(fixture.chunk_delay_s):
                                return
                            if isinstance(chunk, bytes):
                                self.wfile.write(chunk)
                            else:
                                self.write_json(chunk)
                            self.wfile.flush()
                    else:
                        self.write_json({"error": "unexpected endpoint"})
                except (OSError, ValueError):
                    fixture.disconnected.set()

            def write_json(self, value):
                self.wfile.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.025), daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
