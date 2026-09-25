"""Production GIT + report + intake HTTP listener (FR #39).

Binds loopback by default. Never manages Ergo/BobIrcd. Durable queue stays under
BOB_DIGEST_HOME (queue.json).
"""
from __future__ import annotations

import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .intake import FakeGitHubFiler, IntakeConfig, RateLimiter
from .receiver import DigestState, make_handler


def digest_home_from_env(default: Path | None = None) -> Path:
    raw = (os.environ.get("BOB_DIGEST_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if default is not None:
        return Path(default).expanduser().resolve()
    return (Path.home() / ".agentic-irc-bobiverse").resolve()


def jeeves_home_from_env(default: Path | None = None) -> Path:
    raw = (os.environ.get("JEEVES_HOME") or os.environ.get("AGENTIC_IRC_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    if default is not None:
        return Path(default).expanduser().resolve()
    return (Path.home() / ".agentic-irc-jeeves").resolve()


def assert_homes_distinct(jeeves_home: Path, digest_home: Path) -> None:
    a = str(jeeves_home).rstrip("\\/").lower()
    b = str(digest_home).rstrip("\\/").lower()
    if a == b:
        raise RuntimeError(
            f"Jeeves home must differ from digest home ({digest_home}); "
            "sharing would race queue/outbox with ears"
        )


class ProdReceiver:
    """Threading HTTP server for /bob/v1/git|report|intake|digest (FR #47 drop-in)."""

    def __init__(
        self,
        digest_home: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        intake_cfg: IntakeConfig | None = None,
        bob_secret: str | None = None,
        require_secret: bool | None = None,
    ):
        self.digest_home = Path(digest_home)
        self.digest_home.mkdir(parents=True, exist_ok=True)
        # Default ionos port remains 19781 when caller passes it; secret from env/file
        self.state = DigestState(
            self.digest_home,
            intake_cfg=intake_cfg,
            bob_secret=bob_secret,
            require_secret=require_secret,
        )
        if (os.environ.get("GITHUB_TOKEN") or "").strip():
            pass
        self.host = host
        self.port = int(port)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, *, background: bool = True) -> int:
        handler = make_handler(self.state)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = int(self._httpd.server_address[1])
        if background:
            self._thread = threading.Thread(
                target=self._httpd.serve_forever, name="bobjeeves-receiver", daemon=True
            )
            self._thread.start()
        return self.port

    def serve_forever(self) -> None:
        if self._httpd is None:
            self.start(background=False)
        assert self._httpd is not None
        self._httpd.serve_forever()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=3.0)

    def snapshot(self) -> dict[str, Any]:
        return self.state.snapshot()
