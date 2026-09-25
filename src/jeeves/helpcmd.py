"""!help renderer: PM only, one line per command, rate-limited (FR #27)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .commands import (
    ROLE_BOB,
    ROLE_SIMON,
    ROLE_WORKER,
    CommandSpec,
    commands_for_roles,
    get_command,
)
from .length_safe import truncate_utf8, utf8_len
from .nicks import is_worker_nick, parse_worker_nick

HELP_LINE_MAX = 400
HELP_DETAIL_MAX_LINES = 5
HELP_RATE_S = 30.0

_HELP_RE = re.compile(r"^!+\s*help(?:\s+(\S+))?\s*$", re.I)

# Shop grammar pointer (one line; not a Jeeves PM command)
SHOP_POINTER = (
    "note: !bored→Jeeves assign, ACK/DONE/NACK in #{machine} are shop wire, not Jeeves PM — see README"
)


def parse_help(body: str) -> tuple[bool, str | None]:
    """Return (is_help, optional_cmd_name)."""
    m = _HELP_RE.match((body or "").strip())
    if not m:
        return False, None
    arg = m.group(1)
    return True, (arg.lstrip("!").lower() if arg else None)


def is_help(body: str) -> bool:
    ok, _ = parse_help(body)
    return ok


def roles_for_nick(nick: str) -> frozenset[str]:
    """Map nick → help visibility roles."""
    n = (nick or "").strip()
    low = n.lower()
    roles: set[str] = set()
    if low == "simon" or low.startswith("simon-"):
        roles.add(ROLE_SIMON)
        roles.add(ROLE_WORKER)  # simon sees worker cmds too
        roles.add(ROLE_BOB)
    if low.startswith("bob-"):
        roles.add(ROLE_BOB)
        roles.add(ROLE_WORKER)
    if is_worker_nick(n) or parse_worker_nick(n):
        roles.add(ROLE_WORKER)
    if not roles:
        # unknown nick: worker-visible only (safe default)
        roles.add(ROLE_WORKER)
    return frozenset(roles)


def _clip(line: str, max_b: int = HELP_LINE_MAX) -> str:
    line = re.sub(r"\s+", " ", (line or "").replace("\r", " ").replace("\n", " ")).strip()
    if utf8_len(line) <= max_b:
        return line
    return truncate_utf8(line, max_b, ellipsis="...")


def format_index_line(cmd: CommandSpec) -> str:
    who = ",".join(sorted(cmd.roles))
    return _clip(f"{cmd.syntax} - {cmd.summary} [{who}]")


def format_detail_lines(cmd: CommandSpec) -> list[str]:
    """At most 5 lines: syntax, arguments/summary, who, example, related."""
    lines = [
        _clip(f"syntax: {cmd.syntax}"),
        _clip(f"what: {cmd.summary}" + (f" — {cmd.details}" if cmd.details else "")),
        _clip(f"who: {', '.join(sorted(cmd.roles))}"),
    ]
    if cmd.example:
        lines.append(_clip(f"example: {cmd.example}"))
    if cmd.related:
        rel = " ".join(f"!{r}" for r in cmd.related)
        lines.append(_clip(f"related: {rel}"))
    return lines[:HELP_DETAIL_MAX_LINES]


@dataclass
class HelpRateLimit:
    """One !help per nick every HELP_RATE_S seconds."""

    interval_s: float = HELP_RATE_S
    _last: dict[str, float] = field(default_factory=dict)

    def allow(self, nick: str, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        key = (nick or "").lower()
        prev = self._last.get(key)
        if prev is not None and (now - prev) < self.interval_s:
            return False
        self._last[key] = now
        return True

    def remaining_s(self, nick: str, now: float | None = None) -> float:
        now = time.time() if now is None else float(now)
        key = (nick or "").lower()
        prev = self._last.get(key)
        if prev is None:
            return 0.0
        left = self.interval_s - (now - prev)
        return max(0.0, left)


@dataclass
class HelpResult:
    lines: list[str]
    rate_limited: bool = False
    unknown: bool = False


def build_help(
    nick: str,
    cmd_arg: str | None = None,
    *,
    rate: HelpRateLimit | None = None,
    now: float | None = None,
    include_shop_pointer: bool = True,
) -> HelpResult:
    """
    Build PM reply lines for !help / !help <cmd>.
    Never includes channel flood: caller must send each line as PM to nick.
    """
    if rate is not None and not rate.allow(nick, now=now):
        left = int(rate.remaining_s(nick, now=now) + 0.999)
        return HelpResult(
            lines=[_clip(f"rate limit: wait {left}s before !help again")],
            rate_limited=True,
        )

    roles = roles_for_nick(nick)
    visible = commands_for_roles(roles)

    if cmd_arg:
        spec = get_command(cmd_arg)
        if spec is None or not (spec.roles & roles):
            return HelpResult(
                lines=[_clip("unknown command; try !help")],
                unknown=True,
            )
        return HelpResult(lines=format_detail_lines(spec))

    lines = [format_index_line(c) for c in visible]
    if include_shop_pointer:
        lines.append(_clip(SHOP_POINTER))
    return HelpResult(lines=lines)
