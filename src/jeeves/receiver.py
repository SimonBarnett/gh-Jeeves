"""Stub GIT + report + intake webhook receiver for G1 (loopback only)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .announce import process_git_webhook
from .intake import (
    FakeGitHubFiler,
    IntakeConfig,
    RateLimiter,
    get_intake_status,
    process_intake,
)
from .queue import apply_queue_event, load_queue, save_queue


class DigestState:
    def __init__(self, home: Path, *, intake_cfg: IntakeConfig | None = None):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.announces: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.intake_cfg = intake_cfg or IntakeConfig()
        self.intake_filer = FakeGitHubFiler()
        self.intake_rate = RateLimiter(self.intake_cfg.rate_per_min)
        self.intake_logs: list[str] = []

    def chair_outbox_path(self) -> Path:
        return self.home / "chair-outbox.txt"

    def append_outbox(self, line: str) -> None:
        path = self.chair_outbox_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"PRIVMSG #bobiverse :{line}\n")

    def snapshot(self) -> dict[str, Any]:
        q = load_queue(self.home)
        snap: dict[str, Any] = {
            "queue": q,
            "announces": list(self.announces),
            "events": list(self.events),
            "workers": q.get("workers") or {},
        }
        # K5 / FR #6: surface running version + drift (stamp file or live resolve)
        stamp = self.home / "jeeves_version.json"
        if stamp.is_file():
            try:
                snap.update(json.loads(stamp.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        else:
            try:
                from .versioning import version_report_payload

                snap.update(version_report_payload())
            except Exception:
                pass
        return snap


def make_handler(state: DigestState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _read_raw(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b"{}"

        def _read_json(self) -> dict:
            raw = self._read_raw()
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def _send_json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
            if path.startswith("/bob/v1/intake/"):
                iid = path[len("/bob/v1/intake/") :].strip("/")
                if not iid or "/" in iid:
                    self.send_response(404)
                    self.end_headers()
                    return
                with state.lock:
                    code, obj = get_intake_status(state.home, iid)
                self._send_json(code, obj)
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
                        state.events.append(
                            {"event": event, "claim": claim.task, "tag": tag, "id": claim.id}
                        )
                self.send_response(204)
                self.end_headers()
                return
            if path in ("/bob/v1/intake", "/bob/v1/intake/"):
                payload = self._read_json()
                client_ip = self.client_address[0] if self.client_address else "0.0.0.0"
                key = self.headers.get("X-Bob-Intake-Key") or ""
                with state.lock:
                    result = process_intake(
                        state.home,
                        payload,
                        filer=state.intake_filer,
                        cfg=state.intake_cfg,
                        client_ip=client_ip,
                        intake_key_header=key,
                        rate=state.intake_rate,
                    )
                    if result.log_safe:
                        state.intake_logs.append(result.log_safe)
                    state.events.append({"intake": result.body, "status": result.status})
                self._send_json(result.status, result.body)
                return
            if path == "/bob/v1/report":
                payload = self._read_json()
                with state.lock:
                    state.events.append({"report": payload})
                    op = str(payload.get("op") or "")
                    q = load_queue(state.home)
                    nick = str(payload.get("nick") or "")
                    if op == "queue_accept":
                        row = payload.get("accepted_row")
                        if isinstance(row, dict) and row.get("repo") and row.get("id"):
                            rid = str(row.get("id"))
                            rrepo = str(row.get("repo"))
                            rtask = str(row.get("task") or "").upper()
                            q["unaccepted"] = [
                                r
                                for r in (q.get("unaccepted") or [])
                                if not (
                                    str(r.get("repo")) == rrepo
                                    and str(r.get("id")) == rid
                                    and str(r.get("task") or "").upper() == rtask
                                )
                            ]
                            q["accepted"] = [
                                r
                                for r in (q.get("accepted") or [])
                                if not (
                                    str(r.get("repo")) == rrepo
                                    and str(r.get("id")) == rid
                                    and str(r.get("task") or "").upper() == rtask
                                )
                            ]
                            q.setdefault("accepted", []).append(row)
                        if nick:
                            q.setdefault("workers", {})[nick] = {
                                "state": str(payload.get("state") or "busy"),
                                "job": payload.get("job"),
                                "ts": payload.get("ts"),
                            }
                        save_queue(state.home, q)
                    elif op == "worker_state":
                        if nick:
                            q.setdefault("workers", {})[nick] = {
                                "state": str(payload.get("state") or "idle"),
                                "job": payload.get("job"),
                                "ts": payload.get("ts"),
                            }
                            save_queue(state.home, q)
                    elif op == "queue_done":
                        if nick:
                            q.setdefault("workers", {})[nick] = {
                                "state": "idle",
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
    def __init__(
        self,
        home: Path,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        intake_cfg: IntakeConfig | None = None,
    ):
        self.state = DigestState(home, intake_cfg=intake_cfg)
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
