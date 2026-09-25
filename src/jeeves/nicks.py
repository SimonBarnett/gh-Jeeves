"""Worker nick grammar (K2): primary ``{machine}-{pid}``.

Live watch seats use nicks like ``flamingo-46804``, ``marchhare-34992``,
``ce-priority-dev1-12345``. Legacy ``w-<short>-<pid>`` is still accepted and
canonicalised to ``{short}-{pid}``.

Bots (Jeeves, bob-*) are never workers. bored_gate / ear / ACK paths must use
these helpers — never a w-only regex.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

# Live seats: flamingo-46804, marchhare-34992, ce-priority-dev1-99
# Machine segment is greedy up to the final -<digits> pid.
_MACHINE_PID = re.compile(r"^([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)-(\d+)$", re.I)
# Legacy shop workers: w-fla-12345 / w-mh-1
_LEGACY_W = re.compile(r"^w-([a-z0-9]+)-(\d+)$", re.I)

# Never treat these as worker nicks (chair, ears, humans).
_RESERVED_EXACT = frozenset(
    {
        "jeeves",
        "simon",
        "bob",
        "nickserv",
        "chanserv",
    }
)
_RESERVED_PREFIXES = (
    "bob-",
    "jeeves-",
)

DEFAULT_BORED_IDLE_S = 120.0


def parse_worker_nick(nick: str) -> tuple[str, str] | None:
    """Return (machine, pid) or None if not a worker nick."""
    n = (nick or "").strip()
    if not n:
        return None
    low = n.lower()
    if low in _RESERVED_EXACT:
        return None
    if any(low.startswith(p) for p in _RESERVED_PREFIXES):
        return None
    m = _LEGACY_W.match(n)
    if m:
        return m.group(1).lower(), m.group(2)
    m = _MACHINE_PID.match(n)
    if m:
        machine = m.group(1).lower()
        pid = m.group(2)
        # reject pure numeric "machine" or empty
        if not machine or machine.isdigit():
            return None
        return machine, pid
    return None


def is_worker_nick(nick: str) -> bool:
    """True for live ``{machine}-{pid}`` and legacy ``w-<short>-<pid>``."""
    return parse_worker_nick(nick) is not None


def is_live_seat_nick(nick: str) -> bool:
    """True only for primary live grammar ``{machine}-{pid}`` (not legacy w-)."""
    n = (nick or "").strip()
    if not n or _LEGACY_W.match(n):
        return False
    return parse_worker_nick(n) is not None and bool(_MACHINE_PID.match(n))


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


def nick_matches_shop(nick: str, channel: str) -> bool:
    """Worker may only act in their own ``#{machine}`` shop."""
    shop = worker_shop_channel(nick)
    if not shop:
        return False
    return shop == (channel or "").strip().lower()


def is_shop_channel(channel: str) -> bool:
    c = (channel or "").strip().lower()
    return c.startswith("#") and c not in ("#bobiverse", "#agentic_irc")


def bored_gate(
    home: Path | None,
    nick: str,
    channel: str,
    now: float | None = None,
    *,
    idle_s: float = DEFAULT_BORED_IDLE_S,
    last_activity: float | None = None,
    skip_idle_check: bool = False,
) -> str:
    """
    Gate for ear ``!bored`` handling (K2).

    Returns ``ok`` or a reason token: ``not_worker``, ``wrong_shop``, ``wait``.
    Unlike legacy agentic_irc, **accepts** ``{machine}-{pid}`` (e.g. marchhare-34992).
    """
    if not is_worker_nick(nick):
        return "not_worker"
    if not nick_matches_shop(nick, channel):
        return "wrong_shop"
    if skip_idle_check:
        return "ok"
    now = time.time() if now is None else float(now)
    if last_activity is None and home is not None:
        last_activity = _read_last_activity(Path(home), nick)
    if last_activity is None:
        # never seen: allow (idle long enough / first !bored)
        return "ok"
    if (now - float(last_activity)) < float(idle_s):
        return "wait"
    return "ok"


def _activity_path(home: Path) -> Path:
    return Path(home) / "worker_activity.json"


def _read_last_activity(home: Path, nick: str) -> float | None:
    path = _activity_path(home)
    if not path.is_file():
        return None
    try:
        import json

        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    key = canonical_worker_nick(nick) or nick
    raw = doc.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def note_worker_activity(home: Path, nick: str, now: float | None = None) -> None:
    """Record activity so bored_gate can enforce idle (optional for tests)."""
    import json

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = _activity_path(home)
    doc: dict[str, Any] = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                doc = {}
        except (OSError, ValueError):
            doc = {}
    key = canonical_worker_nick(nick) or nick
    doc[key] = float(time.time() if now is None else now)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
