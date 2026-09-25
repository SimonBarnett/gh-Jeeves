"""Parse shop ACK/DONE/NACK/GIVEUP and !bored (ear only)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Allow optional trailing text after the number (FR #102: URL suffix must not
# silently drop the ACK). Number may be #n or n.
_ACK = re.compile(
    r"^ACK\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)(?:\s+(.*))?$",
    re.I,
)
_DONE = re.compile(
    r"^DONE\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)(?:\s+(\S+))?(?:\s+(\S+))?\s*$",
    re.I,
)
_NACK = re.compile(
    r"^(NACK|GIVEUP)\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)(?:\s+(.*))?$",
    re.I,
)
# Prefix that looks like an ACK but failed the full parse (for reject hint).
_ACK_PREFIX = re.compile(r"^ACK\b", re.I)
_ACK_HINT = "format: ACK FR|MRB|UAT owner/repo#N"
# !bored / !BORED / optional trailing junk stripped — ear owns this command.
_BORED = re.compile(r"^!+\s*bored\b", re.I)
# !list with zero or more args (all, repo, fr, …) — FR #50
_LIST = re.compile(r"^!+\s*list(?:\s+.*)?\s*$", re.I)
_HELP = re.compile(r"^!+\s*help(?:\s+\S+)?\s*$", re.I)
_SWEEP = re.compile(r"^!+\s*sweep(?:\s+(#?\S+))?\s*$", re.I)
_IGNORE = re.compile(r"^!+\s*ignore(?:\s+\S+)?\s*$", re.I)
_UNIGNORE = re.compile(r"^!+\s*unignore(?:\s+\S+)?\s*$", re.I)
_IGNORED = re.compile(r"^!+\s*ignored\s*$", re.I)
_STATUS = re.compile(r"^!+\s*status\s*$", re.I)
_RESYNC = re.compile(r"^!+\s*resync\s*$", re.I)
_FOCUS = re.compile(r"^!+\s*focus(?:\s+.*)?\s*$", re.I)
_UNFOCUS = re.compile(r"^!+\s*unfocus(?:\s+.*)?\s*$", re.I)


@dataclass(frozen=True)
class AckMsg:
    task: str
    repo: str
    number: str
    extra: str = ""  # trailing text after number (logged; FR #102)


@dataclass(frozen=True)
class DoneMsg:
    task: str
    repo: str
    number: str
    result: str
    url: str


def is_bored(body: str) -> bool:
    """True for worker idle pings. Ear handles; chair must ignore (K1)."""
    return bool(_BORED.match((body or "").strip()))


def is_list(body: str) -> bool:
    return bool(_LIST.match((body or "").strip()))


def is_help(body: str) -> bool:
    return bool(_HELP.match((body or "").strip()))


def parse_sweep(body: str) -> str | None:
    """Return channel for ``!sweep [#chan]``, or None if not a sweep command.

    Bare ``!sweep`` returns empty string (caller picks default channel).
    """
    m = _SWEEP.match((body or "").strip())
    if not m:
        return None
    ch = m.group(1)
    if not ch:
        return ""
    return ch if ch.startswith("#") else f"#{ch}"


def is_sweep(body: str) -> bool:
    return parse_sweep(body) is not None


def is_ignore(body: str) -> bool:
    return bool(_IGNORE.match((body or "").strip()))


def is_unignore(body: str) -> bool:
    return bool(_UNIGNORE.match((body or "").strip()))


def is_ignored_list(body: str) -> bool:
    return bool(_IGNORED.match((body or "").strip()))


def is_status(body: str) -> bool:
    return bool(_STATUS.match((body or "").strip()))


def is_resync(body: str) -> bool:
    return bool(_RESYNC.match((body or "").strip()))


def is_focus(body: str) -> bool:
    return bool(_FOCUS.match((body or "").strip()))


def is_unfocus(body: str) -> bool:
    return bool(_UNFOCUS.match((body or "").strip()))


def parse_list_filters(body: str) -> tuple[str | None, str | None, bool]:
    """Return (task_filter, repo_filter, list_all) for ``!list ...``."""
    parts = (body or "").strip().split()
    if not parts or not parts[0].lower().lstrip("!").startswith("list"):
        return None, None, False
    task_f = None
    repo_f = None
    list_all = False
    for tok in parts[1:]:
        low = tok.lower()
        if low == "all":
            list_all = True
        elif low in ("fr", "mrb", "uat", "pr", "fix", "build"):
            task_f = low.upper() if low != "pr" else "MRB"
        elif "/" in tok:
            repo_f = tok
    return task_f, repo_f, list_all


def parse_ack(body: str) -> AckMsg | None:
    m = _ACK.match((body or "").strip())
    if not m:
        return None
    extra = (m.group(4) or "").strip()
    return AckMsg(
        task=m.group(1).upper(),
        repo=m.group(2),
        number=m.group(3),
        extra=extra,
    )


def looks_like_ack(body: str) -> bool:
    """True when the line starts with ACK but may still fail parse_ack."""
    return bool(_ACK_PREFIX.match((body or "").strip()))


def ack_format_hint() -> str:
    return _ACK_HINT


def parse_done(body: str) -> DoneMsg | None:
    m = _DONE.match((body or "").strip())
    if not m:
        return None
    return DoneMsg(
        task=m.group(1).upper(),
        repo=m.group(2),
        number=m.group(3),
        result=(m.group(4) or "ok"),
        url=(m.group(5) or ""),
    )


def parse_nack(body: str) -> tuple[str, str, str, str] | None:
    m = _NACK.match((body or "").strip())
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), m.group(3), m.group(4)
