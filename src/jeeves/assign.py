"""FR #106 CAST IRON: Jeeves assigns the next job on worker !bored in #{machine}.

Replaces ear OFFER. Assign line (one wake)::

    <nick>: <FR|MRB|UAT> <owner/repo>#<n> <url>

Empty queue::

    <nick>: nothing queued
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .length_safe import truncate_utf8, utf8_len, wire_line_bytes
from .nicks import (
    canonical_worker_nick,
    is_worker_nick,
    nick_matches_shop,
    parse_worker_nick,
    worker_shop_channel,
)
from .queue import load_queue, ordered_unaccepted, tasks_equivalent, worker_state

log = logging.getLogger("jeeves.assign")

DEFAULT_OFFER_TIMEOUT_S = 300.0  # 5 minutes

_ASSIGN_RE = re.compile(
    r"^(\S+):\s+(FR|MRB|UAT)\s+"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s+(\S+)\s*$",
    re.I,
)
_NOTHING_RE = re.compile(r"^(\S+):\s+nothing queued\s*$", re.I)
_CRLF = re.compile(r"[\r\n]+")


def offers_path(home: Path) -> Path:
    return Path(home) / "offers.json"


def sanitize_assign_text(text: str) -> str:
    t = _CRLF.sub(" ", text or "")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def format_assign_line(
    nick: str,
    row: dict[str, Any],
    *,
    chair_nick: str = "Jeeves",
    channel: str = "#marchhare",
    max_wire: int = 512,
) -> str:
    """Build ``<nick>: <TASK> <repo>#<n> <url>`` (no OFFER / ASSIGN keywords)."""
    task = str(row.get("task") or "FR").upper()
    if task == "PR":
        task = "FR"
    if task not in ("FR", "MRB", "UAT"):
        task = "FR"
    repo = str(row.get("repo") or "").strip()
    ident = str(row.get("id") or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    num = ident.lstrip("#")
    url = str(row.get("url") or "").strip()
    if not url and repo and num:
        kind = "pull" if task == "MRB" else "issues"
        url = f"https://github.com/{repo}/{kind}/{num}"
    prefix = f"{nick}: {task} {repo}#{num} "
    overhead = wire_line_bytes("", nick=chair_nick, channel=channel)
    room = max_wire - overhead - utf8_len(prefix)
    if room < 16:
        room = 16
    url_s = truncate_utf8(url, room, ellipsis="")
    return sanitize_assign_text(prefix + url_s)


def format_nothing_queued(nick: str) -> str:
    return f"{nick}: nothing queued"


def parse_assign_line(text: str) -> dict[str, str] | None:
    t = sanitize_assign_text(text)
    m = _ASSIGN_RE.match(t)
    if not m:
        return None
    return {
        "nick": m.group(1),
        "task": m.group(2).upper(),
        "repo": m.group(3),
        "number": m.group(4),
        "url": m.group(5),
        "text": t,
    }


def is_assign_egress(text: str) -> bool:
    """True if shop egress is a FR #106 assign or empty-queue line (chair-allowed)."""
    t = sanitize_assign_text(text)
    if _NOTHING_RE.match(t):
        return True
    return parse_assign_line(t) is not None


def row_key(row: dict[str, Any]) -> str:
    repo = str(row.get("repo") or "").strip()
    task = str(row.get("task") or "").upper()
    if task == "PR":
        task = "FR"
    ident = str(row.get("id") or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    return f"{repo}|{task}|{ident}"


def author_seat_of(row: dict[str, Any]) -> str:
    for k in ("author_seat", "author_nick", "author"):
        v = str(row.get(k) or "").strip()
        if v and is_worker_nick(v):
            return canonical_worker_nick(v) or v
    # optional Agent: seat in line/body
    blob = f"{row.get('line') or ''} {row.get('body') or ''}"
    m = re.search(r"(?i)\bAgent:\s*([a-z0-9][a-z0-9_-]*-\d+)\b", blob)
    if m and is_worker_nick(m.group(1)):
        return canonical_worker_nick(m.group(1)) or m.group(1)
    return ""


def mrb_blocked_for_author(
    row: dict[str, Any],
    nick: str,
    live_nicks: set[str] | frozenset[str],
) -> bool:
    """Don't offer MRB to PR author seat when another live seat exists."""
    if str(row.get("task") or "").upper() != "MRB":
        return False
    author = author_seat_of(row)
    if not author:
        return False
    me = (canonical_worker_nick(nick) or nick).lower()
    if author.lower() != me:
        return False
    others = {
        (canonical_worker_nick(n) or n).lower()
        for n in live_nicks
        if is_worker_nick(n) and (canonical_worker_nick(n) or n).lower() != me
    }
    return len(others) > 0


@dataclass
class AssignDecision:
    action: str  # assign | nothing | skip | busy | open
    line: str | None = None
    reason: str = ""
    row: dict[str, Any] | None = None


@dataclass
class ChairAssignState:
    """Outstanding offers keyed by worker nick; persisted under home/offers.json."""

    timeout_s: float = DEFAULT_OFFER_TIMEOUT_S
    open: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    _home: Path | None = None

    def bind(self, home: Path) -> None:
        self._home = Path(home)
        self.load()

    def load(self) -> None:
        if self._home is None:
            return
        path = offers_path(self._home)
        if not path.is_file():
            return
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(doc, dict) and isinstance(doc.get("open"), dict):
            self.open = {str(k): dict(v) for k, v in doc["open"].items() if isinstance(v, dict)}

    def save(self) -> None:
        if self._home is None:
            return
        path = offers_path(self._home)
        tmp = path.with_suffix(".tmp")
        body = json.dumps({"v": 1, "open": self.open}, indent=2, sort_keys=True) + "\n"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)

    def clear(self, nick: str) -> None:
        self.open.pop(nick, None)
        self.save()

    def has_open(self, nick: str) -> bool:
        return nick in self.open

    def expire_timed_out(self, now: float | None = None) -> list[str]:
        now_f = time.time() if now is None else float(now)
        expired: list[str] = []
        for nick, ent in list(self.open.items()):
            offered_at = float(ent.get("offered_at") or 0.0)
            if offered_at <= 0 or (now_f - offered_at) >= float(self.timeout_s):
                expired.append(nick)
                self.open.pop(nick, None)
        if expired:
            self.save()
            for n in expired:
                log.info("event=offer_timeout nick=%s", n)
        return expired

    def offered_keys(self) -> set[str]:
        keys: set[str] = set()
        for ent in self.open.values():
            row = ent.get("row") if isinstance(ent.get("row"), dict) else ent
            if isinstance(row, dict) and row.get("repo"):
                keys.add(row_key(row))
        return keys

    def on_ack(self, nick: str) -> None:
        self.clear(nick)

    def on_done(self, nick: str) -> None:
        self.clear(nick)

    def decide(
        self,
        home: Path,
        nick: str,
        channel: str,
        *,
        live_nicks: set[str] | frozenset[str] | None = None,
        chair_nick: str = "Jeeves",
        now: float | None = None,
    ) -> AssignDecision:
        self._home = Path(home)
        self.expire_timed_out(now=now)
        if not is_worker_nick(nick):
            return AssignDecision("skip", reason="not_worker")
        if not nick_matches_shop(nick, channel):
            return AssignDecision("skip", reason="wrong_shop")
        canon = canonical_worker_nick(nick) or nick
        # Busy / already accepted → do not assign another
        if worker_state(home, nick) == "busy":
            return AssignDecision("busy", reason="busy")
        q = load_queue(home)
        for row in q.get("accepted") or []:
            if str(row.get("nick") or "") == nick:
                return AssignDecision("busy", reason="accepted")
        if self.has_open(canon) or self.has_open(nick):
            return AssignDecision("open", reason="one_open_offer")

        live = set(live_nicks or ())
        offered = self.offered_keys()
        accepted_keys = {
            row_key(r)
            for r in (q.get("accepted") or [])
            if isinstance(r, dict)
        }
        pick: dict[str, Any] | None = None
        for row in ordered_unaccepted(home):
            if not isinstance(row, dict):
                continue
            key = row_key(row)
            if key in accepted_keys:
                continue
            if key in offered:
                continue
            if mrb_blocked_for_author(row, nick, live):
                continue
            pick = dict(row)
            break
        if pick is None:
            # Distinguish empty vs all blocked
            if not ordered_unaccepted(home):
                line = format_nothing_queued(nick)
                return AssignDecision("nothing", line=line, reason="empty")
            line = format_nothing_queued(nick)
            return AssignDecision("nothing", line=line, reason="none_eligible")

        # Canonicalise legacy PR → FR for ACK match (#102)
        if str(pick.get("task") or "").upper() == "PR":
            pick["task"] = "FR"
        line = format_assign_line(
            nick, pick, chair_nick=chair_nick, channel=channel
        )
        now_f = time.time() if now is None else float(now)
        self.open[canon] = {
            "row": pick,
            "offered_at": now_f,
            "channel": channel,
            "line": line,
        }
        self.history.append(line)
        self.save()
        return AssignDecision("assign", line=line, reason="ok", row=pick)


def trust_bored(
    nick: str,
    channel: str,
    *,
    is_pm: bool = False,
) -> str:
    """FR #106 trust: worker nick in own shop only. Returns ok or reason."""
    if is_pm:
        return "pm"
    if not is_worker_nick(nick):
        return "not_worker"
    if not nick_matches_shop(nick, channel):
        return "wrong_shop"
    # bots already excluded by is_worker_nick (bob-/jeeves reserved)
    return "ok"


def live_seats_from_modes(
    modes: dict[str, dict[str, str]] | None,
    *,
    shop: str | None = None,
) -> set[str]:
    """Worker nicks present in mode_grants channel maps."""
    out: set[str] = set()
    if not modes:
        return out
    for ch, nicks in modes.items():
        if shop and ch.lower() != shop.lower():
            continue
        if not isinstance(nicks, dict):
            continue
        for nick in nicks.keys():
            if is_worker_nick(nick):
                out.add(canonical_worker_nick(nick) or nick)
    return out
