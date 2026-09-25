"""FR #55: auto-JOIN every server channel via LIST + periodic re-LIST.

Deterministic (no tokens). Denylist skips. KICK rejoin with backoff; ban/invite-only
logs once and stops looping.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .backoff import throttle_delay_s

# RPL_LIST / RPL_LISTEND
_RPL_LIST = re.compile(r"(?i)^\S+\s+322\s+\S+\s+(\S+)\s+(\d+)\s+:")
_RPL_LIST_ALT = re.compile(r"(?i)^:?\S+\s+322\s+\S+\s+(\S+)\s+")
_RPL_LISTEND = re.compile(r"(?i)^\S+\s+323\b")
_KICK = re.compile(r"(?i)^:?(\S+)!\S+\s+KICK\s+(\S+)\s+(\S+)\s+:?(.*)$")
_JOIN = re.compile(r"(?i)^:?(\S+)!\S+\s+JOIN\s+:?(\S+)")
# ERR_BANNEDFROMCHAN / ERR_INVITEONLYCHAN / ERR_CHANNELISFULL / ERR_BADCHANMASK
_JOIN_HARD_FAIL = re.compile(r"(?i)^\S+\s+(474|473|471|476)\s+\S+\s+(\S+)")


def normalize_channel(name: str) -> str:
    c = (name or "").strip()
    if not c:
        return ""
    if not c.startswith("#") and not c.startswith("&"):
        c = "#" + c
    return c


def should_skip_channel(name: str, *, denylist: set[str] | None = None) -> bool:
    """
    Skip empty/local/denylisted names.

    Ergo local channels often start with ``+``; numeric ``0`` is not a channel.
    """
    c = normalize_channel(name)
    if not c or c in ("#", "&"):
        return True
    bare = c.lstrip("#&").lower()
    if bare in ("0",):
        return True
    if c.startswith("+") or bare.startswith("+"):
        return True
    deny = {normalize_channel(x).lower() for x in (denylist or set())}
    if c.lower() in deny:
        return True
    return False


def parse_list_322(line: str) -> str | None:
    """Return channel name from RPL_LIST (322), else None."""
    m = _RPL_LIST.search(line) or _RPL_LIST_ALT.search(line)
    if not m:
        return None
    return normalize_channel(m.group(1))


def is_list_end(line: str) -> bool:
    return bool(_RPL_LISTEND.search(line or ""))


def parse_kick(line: str) -> tuple[str, str, str] | None:
    """Return (kicker, channel, victim) or None."""
    m = _KICK.match((line or "").strip())
    if not m:
        return None
    kicker = m.group(1).split("!")[0]
    return kicker, normalize_channel(m.group(2)), m.group(3)


def parse_join_hard_fail(line: str) -> tuple[str, str] | None:
    """Return (numeric, channel) for ban/invite-only style failures."""
    m = _JOIN_HARD_FAIL.search(line or "")
    if not m:
        return None
    return m.group(1), normalize_channel(m.group(2))


class JoinClient(Protocol):
    def join(self, *channels: str) -> None: ...

    def send_raw(self, line: str) -> None: ...


@dataclass
class AutoJoinState:
    joined: set[str] = field(default_factory=set)
    denylist: set[str] = field(default_factory=set)
    hard_fail: set[str] = field(default_factory=set)  # ban/invite — no rejoin loop
    kick_attempts: dict[str, int] = field(default_factory=dict)
    last_list_channels: set[str] = field(default_factory=set)
    list_runs: int = 0
    events: list[str] = field(default_factory=list)


def channels_to_join(
    listed: set[str],
    state: AutoJoinState,
    *,
    seed: set[str] | None = None,
) -> list[str]:
    """Compute JOIN targets: listed + seed, minus denylist/hard_fail/already joined."""
    want: set[str] = set()
    for ch in listed | (seed or set()):
        n = normalize_channel(ch)
        if not n or should_skip_channel(n, denylist=state.denylist):
            continue
        if n.lower() in {x.lower() for x in state.hard_fail}:
            continue
        want.add(n)
    already = {x.lower() for x in state.joined}
    out = sorted(c for c in want if c.lower() not in already)
    return out


class AutoJoinController:
    """Drives LIST → JOIN and KICK backoff on a chair client."""

    def __init__(
        self,
        client: JoinClient,
        *,
        nick: str,
        denylist: list[str] | None = None,
        seed: list[str] | None = None,
        list_interval_s: float = 60.0,
        on_join: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.nick = nick
        self.state = AutoJoinState(
            denylist={normalize_channel(x).lower() for x in (denylist or [])},
        )
        self.seed = {normalize_channel(x) for x in (seed or ["#bobiverse"]) if x}
        self.list_interval_s = max(5.0, float(list_interval_s))
        self.on_join = on_join
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._list_buf: set[str] = set()
        self._listing = False

    def handle_raw(self, line: str) -> None:
        """Feed server lines (322/323/KICK/47x)."""
        ch = parse_list_322(line)
        if ch:
            self._list_buf.add(ch)
            self._listing = True
            return
        if is_list_end(line) or (self._listing and not ch and "323" in line):
            self._finish_list()
            return
        kick = parse_kick(line)
        if kick:
            _kicker, channel, victim = kick
            if victim.lower() == self.nick.lower():
                self._on_kicked(channel)
            return
        hard = parse_join_hard_fail(line)
        if hard:
            _num, channel = hard
            self.state.hard_fail.add(channel.lower())
            self.state.events.append(f"hard_fail:{channel}")
            return
        # track successful JOIN of self
        m = _JOIN.match((line or "").strip())
        if m and m.group(1).lower() == self.nick.lower():
            c = normalize_channel(m.group(2))
            self.state.joined.add(c)
            self.state.kick_attempts.pop(c.lower(), None)

    def _finish_list(self) -> None:
        import logging

        from .service_log import event as slog

        self._listing = False
        listed = set(self._list_buf)
        self._list_buf.clear()
        self.state.last_list_channels = set(listed)
        self.state.list_runs += 1
        slog(
            logging.getLogger("jeeves.autojoin"),
            "list",
            channels=len(listed),
            run=self.state.list_runs,
        )
        to_join = channels_to_join(listed, self.state, seed=self.seed)
        for ch in to_join:
            self._do_join(ch)

    def _do_join(self, channel: str) -> None:
        import logging

        from .service_log import event as slog

        ch = normalize_channel(channel)
        if should_skip_channel(ch, denylist=self.state.denylist):
            return
        if ch.lower() in {x.lower() for x in self.state.hard_fail}:
            return
        try:
            self.client.join(ch)
        except OSError:
            self.state.events.append(f"join_fail:{ch}")
            slog(logging.getLogger("jeeves.autojoin"), "join_fail", channel=ch)
            return
        self.state.joined.add(ch)
        self.state.events.append(f"join:{ch}")
        slog(logging.getLogger("jeeves.autojoin"), "join", channel=ch)
        if self.on_join:
            try:
                self.on_join(ch)
            except Exception:
                pass

    def request_list(self) -> None:
        self._list_buf.clear()
        self._listing = True
        try:
            self.client.send_raw("LIST")
        except OSError:
            self.state.events.append("list_fail")

    def _on_kicked(self, channel: str) -> None:
        import logging

        from .service_log import event as slog

        ch = normalize_channel(channel)
        self.state.joined.discard(ch)
        # also case-insensitive discard
        self.state.joined = {x for x in self.state.joined if x.lower() != ch.lower()}
        key = ch.lower()
        slog(logging.getLogger("jeeves.autojoin"), "part", channel=ch, reason="kick")
        if key in self.state.hard_fail:
            return
        n = int(self.state.kick_attempts.get(key, 0)) + 1
        self.state.kick_attempts[key] = n
        self.state.events.append(f"kick:{ch}:attempt={n}")
        if n > 8:
            # stop looping after many kicks (likely ban)
            self.state.hard_fail.add(key)
            self.state.events.append(f"kick_giveup:{ch}")
            return
        delay = throttle_delay_s(n, base=1.0, cap=120.0, jitter=0.0)

        def _rejoin() -> None:
            time.sleep(delay)
            if self._stop.is_set():
                return
            if key in self.state.hard_fail:
                return
            self._do_join(ch)

        threading.Thread(target=_rejoin, name=f"rejoin-{ch}", daemon=True).start()

    def start(self, *, list_immediately: bool = True) -> None:
        if list_immediately:
            # seed join first so #bobiverse is never empty during first LIST
            for ch in sorted(self.seed):
                if ch.lower() not in {x.lower() for x in self.state.joined}:
                    self._do_join(ch)
            self.request_list()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jeeves-autojoin", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(self.list_interval_s):
            try:
                self.request_list()
            except Exception:
                continue

    def note_reconnect(self) -> None:
        """On reconnect: clear joined tracking and LIST again (rejoin all)."""
        self.state.joined.clear()
        self.request_list()
