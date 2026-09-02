"""A stand-in Ollama server for end-to-end tests.

Speaks enough of the real HTTP API that the app cannot tell the difference:
/api/generate, /api/tags, /api/ps. Using a real socket rather than patching the
client is the point -- it exercises the HTTP path the app actually takes.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRIEF = {
    "decision": "Ship the smaller thing first.",
    "rationale": ["It is reversible.", "It is cheap.", "It teaches the shape."],
    "dissent": ["The Skeptic wanted the full build."],
    "constraints": ["Must run offline."],
    "open_questions": ["Whether a fourth member helps."],
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # keep pytest output readable

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path == "/api/tags":
            self._send({"models": [{"name": "stub-a"}, {"name": "stub-b"}]})
        elif self.path == "/api/ps":
            self._send({"models": [{"name": "stub-a", "size": 1_048_576}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen.append(request)
        # A request carrying a schema is the chairman's.
        response = (
            json.dumps(BRIEF) if request.get("format")
            else f"opinion from {request.get('model')}"
        )
        self._send({"response": response, "done": True})


class StubOllama:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.seen = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict]:
        return self.server.seen

    def __enter__(self) -> "StubOllama":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()
