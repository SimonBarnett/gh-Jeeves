"""Parse shop ACK/DONE/NACK/GIVEUP and !bored (ear only)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Allow optional trailing punctuation; number may be #n or n.
_ACK = re.compile(
    r"^ACK\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)\s*$",
    re.I,
)
_DONE = re.compile(
    r"^DONE\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)(?:\s+(\S+))?(?:\s+(\S+))?\s*$",
    re.I,
)
_NACK = re.compile(
    r"^(NACK|GIVEUP)\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#?\s*(\d+)\s*$",
    re.I,
)
# !bored / !BORED / optional trailing junk stripped — ear owns this command.
_BORED = re.compile(r"^!+\s*bored\b", re.I)
_LIST = re.compile(r"^!+\s*list\b", re.I)
_HELP = re.compile(r"^!+\s*help\b", re.I)


@dataclass(frozen=True)
class AckMsg:
    task: str
    repo: str
    number: str


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


def parse_list_filters(body: str) -> tuple[str | None, str | None, bool]:
    """Return (task_filter, repo_filter, list_all) for ``!list …``."""
    parts = (body or "").strip().split()
    if not parts or not parts[0].lower().lstrip("!").startswith("list"):
        return None, None, False
    task_f = None
    repo_f = None
    list_all = False
    for p in parts[1:]:
        low = p.lower()
        if low == "all":
            list_all = True
        elif low in ("fr", "mrb", "uat", "pr", "fix", "build"):
            task_f = low.upper() if low != "pr" else "MRB"
        elif "/" in p:
            repo_f = p
    return task_f, repo_f, list_all


def parse_ack(body: str) -> AckMsg | None:
    m = _ACK.match((body or "").strip())
    if not m:
        return None
    return AckMsg(task=m.group(1).upper(), repo=m.group(2), number=m.group(3))


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
