"""Local web page: ask a question, read the answer with its quoted sources.

Binds to 127.0.0.1 only. With the default local model nothing leaves the machine.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .answer import ask
from .llm import Provider
from .search import Index

PAGE = Path(__file__).parent / "static" / "index.html"


def make_handler(index: Index, provider: Provider, embed):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Navigator/0.1"

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/info":
                docs = [{"title": d.title, "jurisdiction": d.jurisdiction, "as_of": d.as_of, "url": d.url} for d in index.docs.values()]
                return self._json(200, {"model": provider.model, "local": provider.name != "anthropic", "passages": len(index.passages), "documents": docs})
            self._json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path != "/api/ask":
                return self._json(404, {"error": "Not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length < 10_000:
                return self._json(400, {"error": "Send a question of reasonable length."})
            try:
                question = str(json.loads(self.rfile.read(length))["question"]).strip()
            except (ValueError, KeyError):
                return self._json(400, {"error": "Expected JSON with a question."})
            if len(question) < 5:
                return self._json(400, {"error": "Ask a full question."})
            self._json(200, ask(question, index, provider, embed).to_dict())

    return Handler


def serve(index: Index, provider: Provider, embed, port: int = 8766) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(index, provider, embed))
    print(f"Procurement Rules Navigator on http://127.0.0.1:{port}  (model: {provider.model}; Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
