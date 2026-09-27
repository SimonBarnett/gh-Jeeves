"""FR #197: !recycle — Jeeves documents + routes; local bob seat executes.

CAST IRON: Jeeves never kills processes, never runs git, never reloads skills.
The local ``bob-{machine}`` seat (tray-equivalent sequence) owns execution.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ordered steps the local bob seat must run (tray-equivalent; scripts only).
RECYCLE_STEPS: tuple[str, ...] = (
    "stop_managed_workers",
    "cleanup_owned_orphans",  # PowerShell/Node/Python owned by this install only
    "git_ff_only",  # fast-forward; leave dirty/diverged intact and report
    "reload_skills",
    "restart_workers",
)

_RECYCLE_RE = re.compile(r"^!+\s*recycle(?:\s+(\S+))?\s*$", re.I)

# Duplicate prevention: one open recycle route per machine within this window.
DEFAULT_RECYCLE_COOLDOWN_S = 120.0


def parse_recycle(body: str) -> tuple[bool, str | None]:
    """Return (is_recycle, optional_machine_or_None for 'all'/id)."""
    m = _RECYCLE_RE.match((body or "").strip())
    if not m:
        return False, None
    arg = (m.group(1) or "").strip().lower() or None
    return True, arg


def is_recycle(body: str) -> bool:
    ok, _ = parse_recycle(body)
    return ok


def shop_machine(channel: str) -> str:
    """``#marchhare`` → ``marchhare``."""
    ch = (channel or "").strip().lower()
    if ch.startswith("#"):
        ch = ch[1:]
    return ch


def format_recycle_route(*, machine: str, requester: str, scope: str = "local") -> str:
    """Shop wire for the local bob seat (not a Jeeves host action).

    Local ear / bob-{machine} matches ``RECYCLE`` and runs Invoke-BobSeatRecycle
    (or tray-equivalent). Jeeves only emits this after auth.
    """
    mid = shop_machine(machine) or "unknown"
    who = (requester or "").strip() or "-"
    sc = (scope or "local").strip().lower()
    return f"RECYCLE machine={mid} by={who} scope={sc} exec=local-bob-seat"


def recycle_steps() -> list[dict[str, str]]:
    """Human/test view of the ordered local workflow."""
    labels = {
        "stop_managed_workers": "Stop managed worker processes for this install",
        "cleanup_owned_orphans": "Clear owned/stale PowerShell, Node, Python for this install only",
        "git_ff_only": "Fast-forward worker checkout (leave dirty/diverged intact; report)",
        "reload_skills": "Reload agent skills",
        "restart_workers": "Restart workers via existing service/tray bootstrap",
    }
    return [{"id": s, "label": labels.get(s, s)} for s in RECYCLE_STEPS]


@dataclass
class RecycleGate:
    """In-memory duplicate / cooldown gate (per digest home process)."""

    cooldown_s: float = DEFAULT_RECYCLE_COOLDOWN_S
    _last: dict[str, float] = field(default_factory=dict)

    def allow(self, machine: str, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        key = shop_machine(machine) or "*"
        prev = self._last.get(key)
        if prev is not None and (now - prev) < self.cooldown_s:
            return False
        self._last[key] = now
        return True

    def remaining_s(self, machine: str, now: float | None = None) -> float:
        now = time.time() if now is None else float(now)
        key = shop_machine(machine) or "*"
        prev = self._last.get(key)
        if prev is None:
            return 0.0
        left = self.cooldown_s - (now - prev)
        return max(0.0, left)


@dataclass
class RecycleDecision:
    ok: bool
    reason: str = ""
    route: str = ""
    pm_lines: list[str] = field(default_factory=list)
    channel_line: str = ""  # optional shop wire; empty if denied/cooldown


def authorize_recycle(
    *,
    nick: str,
    account: str | None,
    owner_account: str,
    allow_bob_ops: bool = True,
) -> tuple[bool, str]:
    """Authorised operator: owner services account, or authenticated bob-* ops.

    Never grant on channel op / server-admin alone. Nick alone is insufficient
    for the owner path (account must match).
    """
    n = (nick or "").strip().lower()
    acct = (account or "").strip().lower()
    owner = (owner_account or "").strip().lower() or "simon"
    if not n:
        return False, "no_nick"
    if n == owner or n.startswith(f"{owner}-"):
        if acct != owner:
            return False, "owner_unauthenticated"
        return True, "owner_account"
    if allow_bob_ops and n.startswith("bob-"):
        # Fleet ear: bob-* nick + present services account (any non-empty).
        if not acct:
            return False, "bob_unauthenticated"
        return True, "bob_ops"
    return False, "denied"


def decide_recycle(
    *,
    nick: str,
    account: str | None,
    channel: str,
    arg: str | None,
    owner_account: str,
    gate: RecycleGate | None = None,
    now: float | None = None,
) -> RecycleDecision:
    """Pure decision: auth + cooldown + route. No process/git side effects."""
    ok, why = authorize_recycle(
        nick=nick, account=account, owner_account=owner_account
    )
    if not ok:
        return RecycleDecision(
            ok=False,
            reason=why,
            pm_lines=["recycle: denied (authorised operator + services account required)"],
        )
    mid = shop_machine(channel)
    scope = "local"
    if arg in ("all", "fleet"):
        scope = "fleet"
        mid = mid or "fleet"
    elif arg:
        mid = shop_machine(arg)
    if not mid:
        return RecycleDecision(
            ok=False,
            reason="no_machine",
            pm_lines=["recycle: usage !recycle [machine|all] (in #{machine} shop)"],
        )
    g = gate or RecycleGate()
    if not g.allow(mid if scope == "local" else "fleet", now=now):
        left = int(g.remaining_s(mid if scope == "local" else "fleet", now=now) + 0.999)
        return RecycleDecision(
            ok=False,
            reason="cooldown",
            pm_lines=[f"recycle: cooldown {left}s (duplicate prevented)"],
        )
    route = format_recycle_route(machine=mid, requester=nick, scope=scope)
    steps = ", ".join(s["id"] for s in recycle_steps())
    return RecycleDecision(
        ok=True,
        reason=why,
        route=route,
        channel_line=route,
        pm_lines=[
            f"recycle: routed to local bob seat ({scope} {mid}); Jeeves runs no host ops",
            f"recycle: steps={steps}",
        ],
    )


def assert_jeeves_has_no_host_ops(source: str) -> list[str]:
    """Lint helper: recycle handler source must not call host killers."""
    banned = (
        "Stop-Process",
        "taskkill",
        "os.kill",
        "subprocess",
        "git fetch",
        "git reset",
        "git pull",
        "Restart-Service",
    )
    hits = [b for b in banned if b.lower() in source.lower()]
    # allow mentioning banned strings in comments/docs strings carefully —
    # only flag if they appear as likely code. For unit test we pass handler source.
    return hits
