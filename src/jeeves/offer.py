"""K11: deterministic single-line OFFER from the ear (ASSIGN has no owner).

Hand/LLM multi-line ASSIGN wakes are forbidden. The bob-{machine} ear owns
exactly one single-line OFFER per worker, gated on busy state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .length_safe import wire_line_bytes, truncate_utf8, utf8_len
from .nicks import canonical_worker_nick, is_worker_nick
from .queue import load_queue, top_unaccepted, worker_state

# Wire grammar (functional-spec): <nick>: OFFER <FR|MRB|UAT> <owner/repo>#<n> <url>
_OFFER_RE = re.compile(
    r"^(\S+):\s+OFFER\s+(FR|MRB|UAT)\s+"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s+(\S+)\s*$",
    re.I,
)
_ASSIGN_RE = re.compile(r"(?i)\bASSIGN\b")
_CRLF = re.compile(r"[\r\n]+")


@dataclass(frozen=True)
class OfferLine:
    nick: str
    task: str
    repo: str
    number: str
    url: str
    text: str  # full single-line body

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}"


def contains_assign(text: str) -> bool:
    """True if body uses forbidden multi-wake ASSIGN keyword."""
    return bool(_ASSIGN_RE.search(text or ""))


def is_single_line(text: str) -> bool:
    t = text or ""
    return "\n" not in t and "\r" not in t


def sanitize_offer_text(text: str) -> str:
    """Collapse any CR/LF/controls to spaces — one IRC PRIVMSG only."""
    t = _CRLF.sub(" ", text or "")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def format_single_line_offer(
    nick: str,
    row: dict[str, Any],
    *,
    ear_nick: str = "bob-flamingo",
    channel: str = "#flamingo",
    max_wire: int = 512,
) -> OfferLine:
    """
    Build one length-safe OFFER line. Never multi-line. Never ASSIGN.
    Title is omitted from the wire (url + ref are vital); keeps one wake.
    """
    task = str(row.get("task") or "FR").upper()
    if task not in ("FR", "MRB", "UAT"):
        task = "FR"
    repo = str(row.get("repo") or "").strip()
    ident = str(row.get("id") or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    num = ident.lstrip("#")
    url = str(row.get("url") or "").strip()
    if not url and repo and num:
        url = f"https://github.com/{repo}/issues/{num}"
    # compact url if needed
    core = f"{nick}: OFFER {task} {repo}#{num} "
    budget = max(32, max_wire - wire_line_bytes(core, nick=ear_nick, channel=channel) - 2)
    # remaining for url
    # wire_line_bytes includes full text; compute room for url alone
    prefix = f"{nick}: OFFER {task} {repo}#{num} "
    overhead = wire_line_bytes("", nick=ear_nick, channel=channel)
    room = max_wire - overhead - utf8_len(prefix)
    if room < 16:
        room = 16
    url_s = truncate_utf8(url, room, ellipsis="")
    text = sanitize_offer_text(prefix + url_s)
    assert is_single_line(text)
    assert not contains_assign(text)
    if wire_line_bytes(text, nick=ear_nick, channel=channel) > max_wire:
        # extreme: drop url host path further
        url_s = truncate_utf8(url_s, max(8, room // 2), ellipsis="")
        text = sanitize_offer_text(prefix + url_s)
    return OfferLine(nick=nick, task=task, repo=repo, number=num, url=url_s, text=text)


def parse_offer(text: str) -> OfferLine | None:
    t = sanitize_offer_text(text)
    m = _OFFER_RE.match(t)
    if not m:
        return None
    return OfferLine(
        nick=m.group(1),
        task=m.group(2).upper(),
        repo=m.group(3),
        number=m.group(4),
        url=m.group(5),
        text=t,
    )


def format_offer(nick: str, row: dict[str, Any], **kwargs: Any) -> str:
    """Back-compat wrapper used by queue/roles — always single-line OFFER."""
    return format_single_line_offer(nick, row, **kwargs).text


@dataclass
class OfferDecision:
    action: str  # offer | nak_busy | nak_open | no_jobs | skip | reject_assign
    line: str | None = None
    reason: str = ""


class EarOfferState:
    """One open OFFER per worker; busy-gated; no multi-line ASSIGN."""

    def __init__(self) -> None:
        self.open: dict[str, dict[str, Any]] = {}  # nick -> row snapshot
        self.history: list[str] = []

    def clear(self, nick: str) -> None:
        self.open.pop(nick, None)

    def clear_all(self) -> None:
        self.open.clear()

    def has_open(self, nick: str) -> bool:
        return nick in self.open

    def decide(
        self,
        home: Path,
        nick: str,
        *,
        machine: str,
        force_busy: bool | None = None,
    ) -> OfferDecision:
        if not is_worker_nick(nick):
            return OfferDecision("skip", reason="not_worker")
        canon = canonical_worker_nick(nick) or nick
        busy = force_busy if force_busy is not None else (worker_state(home, nick) == "busy")
        # also treat accepted job for this nick as busy
        if not busy:
            q = load_queue(home)
            for row in q.get("accepted") or []:
                if str(row.get("nick") or "") == nick:
                    busy = True
                    break
        if busy:
            return OfferDecision("nak_busy", line=f"{nick}: NAK busy", reason="busy")
        if self.has_open(nick):
            return OfferDecision("nak_open", reason="one_open_offer")
        row = top_unaccepted(home)
        if not row:
            return OfferDecision("no_jobs", line=f"{nick}: no jobs", reason="empty")
        offer = format_single_line_offer(
            nick,
            row,
            ear_nick=f"bob-{machine}",
            channel=f"#{machine}",
        )
        if contains_assign(offer.text) or not is_single_line(offer.text):
            return OfferDecision("reject_assign", reason="would_emit_bad_line")
        self.open[nick] = dict(row)
        self.history.append(offer.text)
        return OfferDecision("offer", line=offer.text, reason="ok")

    def on_worker_ack(self, nick: str) -> None:
        self.clear(nick)

    def on_worker_done(self, nick: str) -> None:
        self.clear(nick)


def split_would_multi_wake(text: str) -> bool:
    """True if a payload would become multiple FROM wakes (K11 failure mode)."""
    if not text:
        return False
    if "\n" in text or "\r" in text:
        return True
    # multiple OFFER/ASSIGN verbs on one blob
    if len(re.findall(r"(?i)\b(OFFER|ASSIGN)\b", text)) > 1:
        return True
    return False
