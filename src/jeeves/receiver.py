"""GIT + report + intake webhook receiver (G1 stub + FR #47 bobcallback drop-in)."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .announce import process_git_webhook
from .auth_secret import check_bob_secret, load_bob_secret
from .digest import apply_report, public_digest_snapshot
from .intake import (
    FakeGitHubFiler,
    IntakeConfig,
    RateLimiter,
    get_intake_status,
    process_intake,
)
from .queue import apply_queue_event


class DigestState:
    def __init__(
        self,
        home: Path,
        *,
        intake_cfg: IntakeConfig | None = None,
        bob_secret: str | None = None,
        require_secret: bool | None = None,
    ):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.announces: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.intake_cfg = intake_cfg or IntakeConfig()
        self.intake_filer = FakeGitHubFiler()
        self.intake_rate = RateLimiter(self.intake_cfg.rate_per_min)
        self.intake_logs: list[str] = []
        # FR #47: X-Bob-Secret for report/intake writes (never /bob/v1/git — fleet hooks
        # carry no secret; see docs/vision.md Trust + JEEVES_BRIEF §4.1 / §8).
        # G1 StubReceiver(bob_secret=None): ignore ambient ~/.grok secrets unless
        # BOB_REQUIRE_SECRET / env secret / digest-home bob.secret is present.
        env_secret = load_bob_secret(homes=[self.home])
        if bob_secret is not None:
            self.bob_secret = bob_secret
        else:
            self.bob_secret = env_secret
        if require_secret is None:
            env_req = (os.environ.get("BOB_REQUIRE_SECRET") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            env_explicit = bool(
                (os.environ.get("BOB_CALLBACK_SECRET") or os.environ.get("BOB_SECRET") or "").strip()
            )
            home_secret_file = any(
                (self.home / name).is_file() for name in ("bob.secret", ".bob-secret")
            )
            if bob_secret is not None:
                # explicit constructor arg: empty → off; non-empty → on
                self.require_secret = bool(bob_secret) or env_req
            else:
                # production file under digest home must arm auth (cutover doc)
                self.require_secret = env_explicit or env_req or home_secret_file
            if self.require_secret and not self.bob_secret:
                self.bob_secret = env_secret
        else:
            self.require_secret = bool(require_secret)

    def chair_outbox_path(self) -> Path:
        return self.home / "chair-outbox.txt"

    def append_outbox(self, line: str) -> None:
        path = self.chair_outbox_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"PRIVMSG #bobiverse :{line}\n")

    def snapshot(self) -> dict[str, Any]:
        snap = public_digest_snapshot(self.home, queue_home=self.home)
        snap["announces"] = list(self.announces)
        # keep recent internal events without secrets
        snap.setdefault("events", [])
        if self.events:
            # merge last internal ops into digest events tail (already in digest.json)
            pass
        return snap


def make_handler(state: DigestState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _hdrs(self) -> dict[str, str]:
            return {k: v for k, v in self.headers.items()}

        def _require_write_secret(self) -> bool:
            """FR #47: report/intake POSTs need X-Bob-Secret when require_secret."""
            if not state.require_secret:
                return True
            return check_bob_secret(self._hdrs(), state.bob_secret)

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
            # GET digest is public (bobcallback contract) — no secret
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
            # Report/intake writes need X-Bob-Secret when configured.
            # /bob/v1/git must NOT — fleet GitHub hooks carry no secret (vision Trust;
            # BRIEF: "no HMAC (the fleet hooks carry no secret)").
            if path in ("/bob/v1/report", "/bob/v1/intake", "/bob/v1/intake/"):
                if not self._require_write_secret():
                    self.send_response(401)
                    self.end_headers()
                    return
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
                if not isinstance(payload, dict) or payload == {}:
                    # distinguish empty parse as 400
                    raw = True
                with state.lock:
                    out = apply_report(state.home, payload if isinstance(payload, dict) else {})
                    state.events.append(
                        {"report_op": str((payload or {}).get("op") or ""), "ok": out.ok}
                    )
                    if not out.ok:
                        self.send_response(400)
                        self.end_headers()
                        return
                    if out.body is not None:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(out.body)))
                        self.end_headers()
                        self.wfile.write(out.body)
                        return
                    if not out.changed:
                        self.send_response(200)
                        self.end_headers()
                        return
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
        bob_secret: str | None = None,
        require_secret: bool | None = None,
    ):
        self.state = DigestState(
            home,
            intake_cfg=intake_cfg,
            bob_secret=bob_secret,
            require_secret=require_secret,
        )
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
