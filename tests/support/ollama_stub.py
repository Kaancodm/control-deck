"""Configurable standard-library Ollama HTTP fixture bound to ephemeral loopback."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def ipv6_loopback_available():
    """Report whether a TCP socket can be bound to the IPv6 loopback address."""
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


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
        raw_by_path=None,
        show_extra=None,
        chunked=False,
        ipv6=False,
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
        self.raw_by_path = raw_by_path or {}
        self.show_extra = show_extra or {}
        # chunked frames every body write as one HTTP/1.1 chunk instead of
        # relying on connection close; chunk_delay_s still paces chat chunks.
        self.chunked = chunked
        self.ipv6 = ipv6
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
                    status = fixture.status_by_path.get(self.path, 200)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Connection", "close")
                    if fixture.chunked:
                        self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.close_connection = True
                    if status != 200:
                        self.write_body(b'{"error":"synthetic provider error marker"}')
                    elif self.path in fixture.raw_by_path:
                        self.write_body(fixture.raw_by_path[self.path])
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
                                self.write_body(chunk)
                            else:
                                self.write_json(chunk)
                            self.wfile.flush()
                    else:
                        self.write_json({"error": "unexpected endpoint"})
                    if fixture.chunked:
                        # Only a fully served body gets the terminating chunk.
                        self.wfile.write(b"0\r\n\r\n")
                except (OSError, ValueError):
                    fixture.disconnected.set()

            def write_body(self, data):
                if not fixture.chunked:
                    self.wfile.write(data)
                elif data:
                    # A zero-length chunk would end the body, so skip empty writes.
                    self.wfile.write(b"%x\r\n%b\r\n" % (len(data), data))

            def write_json(self, value):
                self.write_body(json.dumps(value, ensure_ascii=False).encode() + b"\n")

        if ipv6:
            self.server = _IPv6Server(("::1", 0), Handler)
            self.url = f"http://[::1]:{self.server.server_port}"
        else:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.server.daemon_threads = True
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
