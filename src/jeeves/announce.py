"""GitHub → GIT announce line + secret-field filter (not whole-payload).

FR #24: issues/PR lines use length-safe vital-first formatting (`length_safe`).
"""

from __future__ import annotations

import re
from typing import Any

from . import length_safe as ls
from .queue import Claim, claim_from_payload

# Legacy soft cap for ping/push noise lines (still byte-safe via length_safe for issues/PR).
MAX_LINE = 380

# Markers that look like secrets — scanned only on secret-bearing fields.
_SECRET_MARKERS = (
    "BEGIN PRIVATE KEY",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "github_pat_",
    "xoxb-",
    "xoxp-",
    "sk-or-",
    "sk-ant-",
)

_SECRET_FIELDS = frozenset(
    {
        "authorization",
        "token",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "ssh_key",
        "api_key",
        "access_token",
        "refresh_token",
    }
)


def secret_marker_hit(text: str) -> str | None:
    if not text:
        return None
    upper = text if len(text) < 500_000 else text[:500_000]
    for m in _SECRET_MARKERS:
        if m in upper:
            return m
    return None


def _walk_secret_fields(obj: Any, path: str = "") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            child = f"{path}.{key}" if path else key
            if key in _SECRET_FIELDS or key.endswith("_token") or key.endswith("_secret"):
                if isinstance(v, str):
                    hit = secret_marker_hit(v)
                    if hit:
                        return hit
                elif isinstance(v, (dict, list)):
                    hit = _walk_secret_fields(v, child)
                    if hit:
                        return hit
            else:
                hit = _walk_secret_fields(v, child)
                if hit:
                    return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hit = _walk_secret_fields(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def payload_secret_rejected(payload: dict) -> str | None:
    """Return marker if secret-bearing fields contain a marker; ignore title/body mentions."""
    return _walk_secret_fields(payload)


def format_github_webhook_announce(event: str, payload: dict) -> str | None:
    """Single #bobiverse body line. Issues/PR use FR #24 length-safe vitals."""
    repo = (payload.get("repository") or {}).get("full_name") or "unknown/repo"
    event_l = (event or "").lower()
    action = str(payload.get("action") or "")
    actor = str((payload.get("sender") or {}).get("login") or "")

    if event_l == "ping":
        zen = str(payload.get("zen") or "pong")[:80]
        line = f"GIT ping {repo} {zen}"
    elif event_l == "push":
        ref = str(payload.get("ref") or "")
        branch = ref.rsplit("/", 1)[-1] if ref else "unknown"
        sha = str(payload.get("after") or payload.get("head_commit", {}).get("id") or "")[:12]
        commits = payload.get("commits") or []
        n = len(commits) if isinstance(commits, list) else 0
        line = f"GIT push {repo} {branch} {sha} {n} commit(s)"
    elif event_l in ("issues", "pull_request"):
        res = ls.format_github_announce(event_l, payload)
        if not res.ok or not res.line:
            return None
        line = res.line
        # optional actor suffix only if it still fits soft budget
        if actor and " by " not in line:
            cand = f"{line} by {actor}"
            if ls.wire_line_bytes(cand) <= ls.IRC_LINE_MAX_BYTES:
                line = cand
    else:
        line = f"GIT {event_l} {repo}"
        if action:
            line += f" {action}"
        if actor:
            line += f" by {actor}"

    line = re.sub(r"\s+", " ", line).strip()
    if event_l not in ("issues", "pull_request") and len(line) > MAX_LINE:
        line = line[: MAX_LINE - 1] + "…"
    return line


def process_git_webhook(event: str, payload: dict) -> tuple[str | None, Claim | None, str | None]:
    """
    Returns (announce_line, claim, reject_reason).
    reject_reason set → do not announce or enqueue.
    """
    hit = payload_secret_rejected(payload)
    if hit:
        return None, None, f"secret_field:{hit}"
    line = format_github_webhook_announce(event, payload)
    claim = claim_from_payload(event, payload)
    return line, claim, None
