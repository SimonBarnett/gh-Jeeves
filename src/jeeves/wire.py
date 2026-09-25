"""Parse shop ACK/DONE/NACK/GIVEUP and !bored (ear only)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ACK = re.compile(
    r"^ACK\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s*$",
    re.I,
)
_DONE = re.compile(
    r"^DONE\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)(?:\s+(\S+))?(?:\s+(\S+))?\s*$",
    re.I,
)
_NACK = re.compile(
    r"^(NACK|GIVEUP)\s+(FR|MRB|UAT)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s*$",
    re.I,
)
_BORED = re.compile(r"^!bored\s*$", re.I)
_LIST = re.compile(r"^!list\s*$", re.I)


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
    return bool(_BORED.match((body or "").strip()))


def is_list(body: str) -> bool:
    return bool(_LIST.match((body or "").strip()))


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
