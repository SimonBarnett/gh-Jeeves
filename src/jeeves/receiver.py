"""Stub GIT + report webhook receiver for G1 (loopback only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .announce import process_git_webhook
from .queue import apply_queue_event, load_queue, save_queue


class DigestState:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.announces: list[str] = []
        self.events: list[dict[str, Any]] = []

    def chair_outbox_path(self) -> Path:
        return self.home / "chair-outbox.txt"

    def append_outbox(self, line: str) -> None:
        path = self.chair_outbox_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"PRIVMSG #bobiverse :{line}\n")

    def snapshot(self) -> dict[str, Any]:
        q = load_queue(self.home)
        return {
            "queue": q,
            "announces": list(self.announces),
            "events": list(self.events),
            "workers": q.get("workers") or {},
        }


def make_handler(state: DigestState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/bob/v1/report", "/bob/v1/digest", "/digest"):
                body = json.dumps(state.snapshot()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/bob/v1/git":
                event = self.headers.get("X-GitHub-Event") or ""
                payload = self._read_json()
                with state.lock:
                    line, claim, reject = process_git_webhook(event, payload)
                    if reject:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(reject.encode("utf-8"))
                        return
                    if line:
                        state.announces.append(line)
                        state.append_outbox(line)
                    if claim is not None:
                        tag = apply_queue_event(state.home, claim)
                        state.events.append({"event": event, "claim": claim.task, "tag": tag, "id": claim.id})
                self.send_response(204)
                self.end_headers()
                return
            if path == "/bob/v1/report":
                payload = self._read_json()
                with state.lock:
                    state.events.append({"report": payload})
                    # merge worker busy/idle from Jeeves writer
                    op = str(payload.get("op") or "")
                    if op == "worker_state":
                        q = load_queue(state.home)
                        nick = str(payload.get("nick") or "")
                        if nick:
                            q.setdefault("workers", {})[nick] = {
                                "state": str(payload.get("state") or "idle"),
                                "job": payload.get("job"),
                                "ts": payload.get("ts"),
                            }
                            save_queue(state.home, q)
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    return Handler


class StubReceiver:
    def __init__(self, home: Path, host: str = "127.0.0.1", port: int = 0):
        self.state = DigestState(home)
        self.host = host
        self.port = port
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> int:
        handler = make_handler(self.state)
        self._httpd = HTTPServer((self.host, self.port), handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
