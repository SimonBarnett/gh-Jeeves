"""FR #52: Jeeves grants +h to authenticated bob-* and +o to authenticated simon.

Trust is by **services account** (SASL / account-notify), never nick alone.
No channel text. Idempotent MODE. Workers get nothing.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

# Default fleet machine ids (shop names without #)
DEFAULT_FLEET_MACHINES = frozenset(
    {
        "flamingo",
        "marchhare",
        "ionos",
        "ce-priority-dev1",
        "dev1",
    }
)

_JOIN = re.compile(
    r"(?i)^:?(\S+)(?:!(\S+))?@(\S+)\s+JOIN\s+:?(\S+)(?:\s+(\S+)\s+:?(.*))?$"
)
# extended-join: JOIN #chan account :realname  OR JOIN #chan
_MODE = re.compile(r"(?i)^:?\S+\s+MODE\s+(\S+)\s+(\S+)(?:\s+(.*))?$")
_ACCOUNT_NOTIFY = re.compile(r"(?i)^:?(\S+)!\S+\s+ACCOUNT\s+(\S+)")
_WHO_315 = re.compile(r"(?i)^\S+\s+315\b")  # end of WHO
# 354 WHOX custom — simplified: nick account host flags
_WHOX = re.compile(r"(?i)^\S+\s+354\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)")


@dataclass(frozen=True)
class Presence:
    """Identity of a nick in a channel."""

    nick: str
    account: str | None  # services account; None / "*" = unauthenticated
    host: str = ""
    channel: str = ""
    modes: str = ""  # current prefix modes e.g. "ohv"

    @property
    def authed(self) -> bool:
        a = (self.account or "").strip()
        return bool(a) and a not in ("*", "0", "1")


def is_bob_nick(nick: str) -> bool:
    n = (nick or "").strip().lower()
    return n.startswith("bob-") and len(n) > 4


def bob_machine(nick: str) -> str | None:
    if not is_bob_nick(nick):
        return None
    return (nick or "").strip().lower()[4:]


def is_simon_nick(nick: str) -> bool:
    return (nick or "").strip().lower() == "simon"


def is_worker_style(nick: str) -> bool:
    """{machine}-{pid} workers — never grant modes."""
    n = (nick or "").strip().lower()
    if is_bob_nick(n) or is_simon_nick(n) or n == "jeeves":
        return False
    # trailing -digits
    if re.match(r"^[a-z0-9][a-z0-9_-]*-\d+$", n):
        return True
    return False


def simon_host_ok(host: str, fleet: frozenset[str] = DEFAULT_FLEET_MACHINES) -> bool:
    """Simon must appear from a fleet machine host/cloak containing a machine id."""
    h = (host or "").strip().lower()
    if not h:
        return False
    for mid in fleet:
        if mid.lower() in h:
            return True
    return False


def desired_mode(
    p: Presence,
    *,
    fleet: frozenset[str] = DEFAULT_FLEET_MACHINES,
) -> str | None:
    """
    Return single mode char to grant ('h' or 'o'), or None.

    - bob-* authenticated → +h; in own #{machine} → +o (durable shop op)
    - simon authenticated + fleet host → +o
    - workers / imposters → None
    """
    nick = (p.nick or "").strip()
    if is_worker_style(nick):
        return None
    ch = (p.channel or "").strip().lower()

    if is_bob_nick(nick):
        if not p.authed:
            return None
        # account should match bob nick or bob-{machine} services name
        acct = (p.account or "").strip().lower()
        nlow = nick.lower()
        if acct not in (nlow, nlow.replace("bob-", ""), "bob"):
            # allow account == full nick or machine id for bob-flamingo → flamingo
            mid = bob_machine(nick)
            if acct not in (nlow, mid or "", f"bob-{mid}" if mid else ""):
                # still allow if account equals nick exactly after normalize
                if acct != nlow:
                    return None
        mid = bob_machine(nick)
        if mid and ch in (f"#{mid}", f"#{mid.lower()}"):
            return "o"  # durable op in own shop
        return "h"

    if is_simon_nick(nick):
        if not p.authed:
            return None
        if (p.account or "").strip().lower() != "simon":
            return None
        if not simon_host_ok(p.host, fleet=fleet):
            return None
        return "o"

    return None


def already_has_mode(modes: str, want: str) -> bool:
    """True if nick already has want, or a higher mode (o > h > v)."""
    m = (modes or "").lower()
    w = (want or "").lower()
    if w in m:
        return True
    # op implies half-op for our purposes (don't downgrade)
    if w == "h" and "o" in m:
        return True
    return False


def mode_line(channel: str, mode: str, nick: str) -> str:
    """IRC MODE command — no channel text."""
    ch = channel if channel.startswith("#") else f"#{channel}"
    return f"MODE {ch} +{mode} {nick}"


@dataclass
class ModeGrantState:
    # channel.lower() -> nick.lower() -> modes string
    modes: dict[str, dict[str, str]] = field(default_factory=dict)
    # nick.lower() -> account
    accounts: dict[str, str] = field(default_factory=dict)
    # nick.lower() -> host
    hosts: dict[str, str] = field(default_factory=dict)
    # one-shot log for unauth
    unauth_logged: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)
    mode_sent: list[str] = field(default_factory=list)
    last_mode_ts: float = 0.0


class ModeClient(Protocol):
    def send_raw(self, line: str) -> None: ...


class ModeGrantController:
    """Apply FR #52 grants from JOIN / account-notify / sweep."""

    def __init__(
        self,
        client: ModeClient,
        *,
        jeeves_nick: str = "Jeeves",
        fleet: frozenset[str] | None = None,
        rate_s: float = 0.8,
        clock: Callable[[], float] | None = None,
    ):
        self.client = client
        self.jeeves_nick = jeeves_nick
        self.fleet = fleet or DEFAULT_FLEET_MACHINES
        self.rate_s = float(rate_s)
        self._clock = clock or time.time
        self.state = ModeGrantState()
        self._lock = threading.Lock()

    def set_account(self, nick: str, account: str | None) -> None:
        key = (nick or "").strip().lower()
        if not key:
            return
        if account and account not in ("*", "0"):
            self.state.accounts[key] = account.strip()
        else:
            self.state.accounts.pop(key, None)

    def set_host(self, nick: str, host: str) -> None:
        key = (nick or "").strip().lower()
        if key:
            self.state.hosts[key] = (host or "").strip()

    def note_mode(self, channel: str, modespec: str, nicks: list[str]) -> None:
        """Track MODE from server (e.g. +h bob-x)."""
        ch = (channel or "").strip().lower()
        if not ch.startswith("#"):
            ch = "#" + ch
        # parse +oh nick1 nick2
        spec = (modespec or "").strip()
        if not spec:
            return
        adding = True
        ni = 0
        bucket = self.state.modes.setdefault(ch, {})
        args = list(nicks)
        for ch_m in spec:
            if ch_m == "+":
                adding = True
                continue
            if ch_m == "-":
                adding = False
                continue
            if ch_m in "ohvqa":
                if ni >= len(args):
                    break
                nick = args[ni].lower()
                ni += 1
                cur = set(bucket.get(nick, ""))
                if adding:
                    cur.add(ch_m)
                else:
                    cur.discard(ch_m)
                bucket[nick] = "".join(sorted(cur))

    def presence_for(self, nick: str, channel: str) -> Presence:
        key = (nick or "").strip().lower()
        ch = (channel or "").strip().lower()
        if not ch.startswith("#"):
            ch = "#" + ch
        return Presence(
            nick=nick,
            account=self.state.accounts.get(key),
            host=self.state.hosts.get(key, ""),
            channel=ch,
            modes=self.state.modes.get(ch, {}).get(key, ""),
        )

    def maybe_grant(self, nick: str, channel: str) -> str | None:
        """Decide and send MODE if needed. Returns mode char or None."""
        p = self.presence_for(nick, channel)
        want = desired_mode(p, fleet=self.fleet)
        if want is None:
            if (is_bob_nick(nick) or is_simon_nick(nick)) and not p.authed:
                key = f"{nick.lower()}@{channel.lower()}"
                if key not in self.state.unauth_logged:
                    self.state.unauth_logged.add(key)
                    self.state.events.append(f"unauth_skip:{nick}:{channel}")
            return None
        if already_has_mode(p.modes, want):
            self.state.events.append(f"idempotent:{nick}:{channel}:+{want}")
            return None
        # rate limit
        now = self._clock()
        wait = self.rate_s - (now - self.state.last_mode_ts)
        if wait > 0:
            time.sleep(wait)
        line = mode_line(channel, want, nick)
        try:
            self.client.send_raw(line)
        except OSError:
            self.state.events.append(f"mode_fail:{line}")
            return None
        self.state.last_mode_ts = self._clock()
        self.state.mode_sent.append(line)
        # optimistic track
        self.note_mode(channel, f"+{want}", [nick])
        self.state.events.append(f"grant:{nick}:{channel}:+{want}")
        return want

    def on_join(
        self,
        nick: str,
        channel: str,
        *,
        account: str | None = None,
        host: str = "",
    ) -> str | None:
        if account is not None:
            self.set_account(nick, account)
        if host:
            self.set_host(nick, host)
        if (nick or "").lower() == self.jeeves_nick.lower():
            # own join — sweep later via NAMES
            self.state.events.append(f"self_join:{channel}")
            return None
        return self.maybe_grant(nick, channel)

    def sweep_channel(self, channel: str, nicks: list[str]) -> list[str]:
        """Re-grant for nicks already present (rejoin / NAMES)."""
        granted: list[str] = []
        for n in nicks:
            if (n or "").lower() == self.jeeves_nick.lower():
                continue
            g = self.maybe_grant(n, channel)
            if g:
                granted.append(f"{n}:+{g}")
        return granted

    def handle_raw(self, line: str) -> None:
        """Parse JOIN / ACCOUNT / MODE from the wire."""
        s = (line or "").strip()
        m = _ACCOUNT_NOTIFY.match(s)
        if m:
            nick = m.group(1).split("!")[0]
            acct = m.group(2)
            if acct.upper() == "*":
                self.set_account(nick, None)
            else:
                self.set_account(nick, acct)
            return
        m = _JOIN.match(s)
        if m:
            nick = m.group(1).split("!")[0]
            user = m.group(2) or ""
            host = m.group(3) or ""
            channel = m.group(4)
            # extended-join: account in group 5
            account = m.group(5) if m.lastindex and m.lastindex >= 5 else None
            if account and account.startswith(":"):
                account = None
            if host:
                self.set_host(nick, host)
            if account and account not in ("*", "0"):
                self.set_account(nick, account)
            self.on_join(nick, channel, account=self.state.accounts.get(nick.lower()), host=host)
            return
        # MODE tracking from others / self
        if " MODE " in f" {s} ":
            parts = s.split()
            try:
                idx = next(i for i, p in enumerate(parts) if p.upper() == "MODE")
                ch = parts[idx + 1]
                spec = parts[idx + 2]
                nicks = parts[idx + 3 :]
                # strip leading :
                nicks = [n.lstrip(":") for n in nicks]
                self.note_mode(ch, spec, nicks)
            except (StopIteration, IndexError):
                pass
