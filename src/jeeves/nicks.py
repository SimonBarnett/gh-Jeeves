"""Worker nick grammar: {machine}-{pid} (and legacy w-<short>-<pid>)."""

from __future__ import annotations

import re

# Live seats: flamingo-46804, marchhare-34992
_MACHINE_PID = re.compile(r"^([a-z0-9][a-z0-9_-]*?)-(\d+)$", re.I)
# Legacy shop workers: w-fla-12345
_LEGACY_W = re.compile(r"^w-([a-z0-9]+)-(\d+)$", re.I)


def parse_worker_nick(nick: str) -> tuple[str, str] | None:
    n = (nick or "").strip()
    if not n:
        return None
    m = _LEGACY_W.match(n)
    if m:
        return m.group(1).lower(), m.group(2)
    m = _MACHINE_PID.match(n)
    if m:
        return m.group(1).lower(), m.group(2)
    return None


def canonical_worker_nick(nick: str) -> str | None:
    parsed = parse_worker_nick(nick)
    if not parsed:
        return None
    machine, pid = parsed
    return f"{machine}-{pid}"


def worker_shop_channel(nick: str) -> str | None:
    parsed = parse_worker_nick(nick)
    if not parsed:
        return None
    return f"#{parsed[0].lower()}"


def is_shop_channel(channel: str) -> bool:
    c = (channel or "").strip().lower()
    return c.startswith("#") and c not in ("#bobiverse", "#agentic_irc")
