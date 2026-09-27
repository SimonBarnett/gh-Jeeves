"""Shared helpers for FR #204 endpoint smoke scripts (stdlib only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# CAST IRON: scripts default to the canonical public host (FR #204).
DEFAULT_BASE_URL = "https://bob.ntsa.uk"

SECRET_ENV_KEYS = (
    "BOB_SECRET",
    "X_BOB_SECRET",
    "JEEVES_BOB_SECRET",
    "BOB_INTAKE_KEY",
)


def redact(s: str) -> str:
    """Never echo secrets in output."""
    out = s or ""
    for key in SECRET_ENV_KEYS:
        val = os.environ.get(key) or ""
        if val and len(val) >= 4:
            out = out.replace(val, "***")
    return out


def get_secret() -> str:
    return (
        os.environ.get("BOB_SECRET")
        or os.environ.get("X_BOB_SECRET")
        or os.environ.get("JEEVES_BOB_SECRET")
        or ""
    ).strip()


def get_intake_key() -> str:
    return (os.environ.get("BOB_INTAKE_KEY") or "").strip()


def base_url_from_env(cli: str | None = None) -> str:
    if cli and cli.strip():
        return cli.strip().rstrip("/")
    env = (os.environ.get("BOB_SMOKE_BASE_URL") or os.environ.get("JEEVES_SMOKE_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    return DEFAULT_BASE_URL.rstrip("/")


def add_base_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--base-url",
        default=None,
        help=f"API base (default {DEFAULT_BASE_URL}; env BOB_SMOKE_BASE_URL)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable JSON summary")
    p.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout seconds")


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, str], bytes]:
    h = dict(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        data = e.read() if hasattr(e, "read") else b""
        return int(e.code), {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, data
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ConnectionError(redact(str(e))) from e


def json_body(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


def parse_json(data: bytes) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_raw": redact(data[:200].decode("utf-8", errors="replace"))}


class CheckResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = True
        self.checks: list[dict[str, Any]] = []

    def add(self, label: str, passed: bool, detail: str = "") -> None:
        self.checks.append(
            {"label": label, "ok": bool(passed), "detail": redact(detail or "")}
        )
        if not passed:
            self.ok = False

    def as_dict(self) -> dict[str, Any]:
        return {"group": self.name, "ok": self.ok, "checks": self.checks}


def finish(result: CheckResult, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        status = "PASS" if result.ok else "FAIL"
        print(f"{result.name}: {status}")
        for c in result.checks:
            mark = "ok" if c["ok"] else "FAIL"
            extra = f" — {c['detail']}" if c["detail"] else ""
            print(f"  [{mark}] {c['label']}{extra}")
    return 0 if result.ok else 1
