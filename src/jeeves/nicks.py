"""Worker nick grammar (K2): primary form is ``{machine}-{pid}``.

Live watch seats use nicks like ``flamingo-46804``, ``marchhare-34992``,
``ce-priority-dev1-12345``. Legacy ``w-<short>-<pid>`` is still parsed and
canonicalised to ``{short}-{pid}`` so old workers are not dropped, but new
code and tests treat ``{machine}-{pid}`` as the one grammar.

K2 fix: claim / !bored / ACK paths must accept real seats — never require
only ``w-<short>-<pid>`` (that was the agentic_irc bored_gate bug).
"""

from __future__ import annotations

import re

# Live seats: flamingo-46804, marchhare-34992, ce-priority-dev1-61432
# Machine may contain hyphens; pid is the final all-digit segment.
_MACHINE_PID = re.compile(r"^([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)-(\d+)$", re.I)
# Legacy shop workers: w-fla-12345 → canonical fla-12345
_LEGACY_W = re.compile(r"^w-([a-z0-9]+)-(\d+)$", re.I)

# Fleet service / ear / chair nicks are never workers (even if they match shape).
_RESERVED_EXACT = frozenset(
    {
        "jeeves",
        "nickserv",
        "chanserv",
        "simon",
    }
)
_RESERVED_PREFIXES = (
    "bob-",
    "jeeves-",
    "sniff-",
)


def _reserved(nick: str) -> bool:
    n = nick.lower()
    if n in _RESERVED_EXACT:
        return True
    return any(n.startswith(p) for p in _RESERVED_PREFIXES)


def parse_worker_nick(nick: str) -> tuple[str, str] | None:
    """Return (machine, pid) for a worker nick, else None.

    Primary: ``{machine}-{pid}``. Legacy: ``w-<short>-<pid>``.
    """
    n = (nick or "").strip()
    if not n or _reserved(n):
        return None
    m = _LEGACY_W.match(n)
    if m:
        return m.group(1).lower(), m.group(2)
    m = _MACHINE_PID.match(n)
    if m:
        machine = m.group(1).lower()
        pid = m.group(2)
        # Guard: bare "w-123" is not a machine seat
        if machine == "w":
            return None
        return machine, pid
    return None


def is_worker_nick(nick: str) -> bool:
    """True if nick is a claimable worker under the K2 grammar."""
    return parse_worker_nick(nick) is not None


def canonical_worker_nick(nick: str) -> str | None:
    """Always ``{machine}-{pid}`` (legacy w-* mapped)."""
    parsed = parse_worker_nick(nick)
    if not parsed:
        return None
    machine, pid = parsed
    return f"{machine}-{pid}"


def worker_shop_channel(nick: str) -> str | None:
    """Own shop ``#{machine}`` for this worker."""
    parsed = parse_worker_nick(nick)
    if not parsed:
        return None
    return f"#{parsed[0].lower()}"


def is_shop_channel(channel: str) -> bool:
    c = (channel or "").strip().lower()
    return c.startswith("#") and c not in ("#bobiverse", "#agentic_irc")


def worker_in_own_shop(nick: str, channel: str) -> bool:
    """True when PRIVMSG target is the worker's own #{machine}."""
    shop = worker_shop_channel(nick)
    if not shop:
        return False
    return (channel or "").strip().lower() == shop


def bored_gate(nick: str, channel: str) -> str:
    """
    Ear-side gate formerly broken on w-* only (K2).

    Returns:
      ``ok`` — worker nick in own shop
      ``not_worker`` — not a worker nick (Jeeves, bob-*, plain names)
      ``wrong_shop`` — worker but not in #{machine}
    """
    if not is_worker_nick(nick):
        return "not_worker"
    if not worker_in_own_shop(nick, channel):
        return "wrong_shop"
    return "ok"
