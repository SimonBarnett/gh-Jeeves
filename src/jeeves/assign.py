"""FR #106 CAST IRON: Jeeves assigns the next job on worker !bored in #{machine}.

Replaces ear OFFER. Assign line (one wake)::

    <nick>: <FR|MRB|UAT> <owner/repo>#<n> <url>

Empty queue::

    <nick>: nothing queued
"""

from __future__ import annotations

import json
import logging
import os
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
from .capability import row_blocked_for_machine
from .queue import (
    fr_already_done,
    is_mrb_fix_pr_title,
    load_queue,
    ordered_unaccepted,
    row_skip_fr_reason,
    save_queue,
    tasks_equivalent,
    worker_state,
)

log = logging.getLogger("jeeves.assign")

# CAST IRON: exclusive open offer per row_key; only re-offer after this timeout
# with no ACK (Simon 2026-09-27: 90s for the worker to respond).
DEFAULT_OFFER_TIMEOUT_S = 90.0
# FR #133: after this many timed-out offers of the same job to the same seat, skip it.
DEFAULT_MAX_OFFER_ATTEMPTS = 3


def offer_timeout_s_from_env(default: float = DEFAULT_OFFER_TIMEOUT_S) -> float:
    """Optional ``JEEVES_OFFER_TIMEOUT_S`` override (seconds, min 1)."""
    raw = (os.environ.get("JEEVES_OFFER_TIMEOUT_S") or "").strip()
    if not raw:
        return float(default)
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(default)

_ASSIGN_RE = re.compile(
    r"^(\S+):\s+(FR|MRB|UAT)\s+"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s+(\S+)\s*$",
    re.I,
)
_NOTHING_RE = re.compile(r"^(\S+):\s+nothing queued\s*$", re.I)
_CRLF = re.compile(r"[\r\n]+")
# bobiverse#247: MRB offers must use a real pull URL — never invent /pull/{issue_id}.
_PR_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)",
    re.I,
)
_ISSUE_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)",
    re.I,
)


def offers_path(home: Path) -> Path:
    return Path(home) / "offers.json"


def sanitize_assign_text(text: str) -> str:
    t = _CRLF.sub(" ", text or "")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm_task(row: dict[str, Any]) -> str:
    task = str(row.get("task") or "FR").upper()
    if task == "PR":
        task = "FR"
    if task not in ("FR", "MRB", "UAT"):
        task = "FR"
    return task


def _row_num(row: dict[str, Any]) -> str:
    ident = str(row.get("id") or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    return ident.lstrip("#")


def resolve_assign_url(row: dict[str, Any]) -> str:
    """URL for an assign line.

    FR/UAT may invent ``/issues/{id}`` when url is missing.
    MRB may only use an existing ``/pull/N`` url, or invent from explicit
    ``pr_id`` / ``pr`` — never from the bare row id alone (bobiverse#247).
    """
    task = _norm_task(row)
    repo = str(row.get("repo") or "").strip()
    url = str(row.get("url") or "").strip()
    if task == "MRB":
        if _PR_URL_RE.search(url):
            return url
        # Explicit PR id only — do not fall back to issue id.
        pr = str(row.get("pr_id") or row.get("pr") or "").strip().lstrip("#")
        if repo and pr:
            return f"https://github.com/{repo}/pull/{pr}"
        # Missing / issues-shaped url → empty (caller skips offer).
        return ""
    if url:
        return url
    num = _row_num(row)
    if repo and num:
        return f"https://github.com/{repo}/issues/{num}"
    return ""


def mrb_row_offerable(
    row: dict[str, Any],
    *,
    pr_exists: Callable[[str, str], bool] | None = None,
) -> bool:
    """True when an MRB row has a resolvable pull URL (and optional live PR check).

    The pull URL's owner/repo must match the queue row's ``repo`` (cross-repo
    pull URLs are not offerable — bobiverse#247 / MRB #229 harden).
    """
    if _norm_task(row) != "MRB":
        return True
    url = resolve_assign_url(row)
    m = _PR_URL_RE.search(url) if url else None
    if not url or not m:
        return False
    if _ISSUE_URL_RE.search(str(row.get("url") or "")) and not _PR_URL_RE.search(
        str(row.get("url") or "")
    ):
        return False
    row_repo = str(row.get("repo") or "").strip()
    url_repo = m.group(1)
    if row_repo and url_repo.lower() != row_repo.lower():
        return False
    if pr_exists is None:
        return True
    repo, num = m.group(1), m.group(2)
    try:
        return bool(pr_exists(repo, num))
    except Exception as e:  # noqa: BLE001 — offer path must not crash
        log.warning(
            "event=mrb_pr_exists_err repo=%s num=%s err=%s",
            repo,
            num,
            type(e).__name__,
        )
        # Fail closed for MRB: do not offer a possibly-fake pull URL.
        return False


def format_assign_line(
    nick: str,
    row: dict[str, Any],
    *,
    chair_nick: str = "Jeeves",
    channel: str = "#marchhare",
    max_wire: int = 512,
) -> str:
    """Build ``<nick>: <TASK> <repo>#<n> <url>`` (no OFFER / ASSIGN keywords)."""
    task = _norm_task(row)
    repo = str(row.get("repo") or "").strip()
    ident = str(row.get("id") or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    num = ident.lstrip("#")
    url = resolve_assign_url(row)
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
    """Primary blocked seat (legacy); prefer ``author_seats_of`` (gh-Jeeves#230)."""
    seats = author_seats_of(row)
    return seats[0] if seats else ""


def author_seats_of(row: dict[str, Any]) -> list[str]:
    """All seats that must not self-MRB / self-UAT this row (bobiverse#240 / #265 / gh-Jeeves#230)."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        v = str(raw or "").strip()
        if not v or not is_worker_nick(v):
            return
        nick = canonical_worker_nick(v) or v
        key = nick.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(nick)

    for k in ("implementer_seat", "mrb_author_seat", "author_seat", "author_nick", "author"):
        _add(str(row.get(k) or ""))
    multi = row.get("author_seats")
    if isinstance(multi, (list, tuple)):
        for item in multi:
            _add(str(item or ""))
    elif multi:
        for part in str(multi).replace(";", ",").split(","):
            _add(part)
    blob = f"{row.get('line') or ''} {row.get('body') or ''}"
    m = re.search(r"(?i)\bAgent:\s*([a-z0-9][a-z0-9_-]*-\d+)\b", blob)
    if m:
        _add(m.group(1))
    return found


def _live_worker_nicks(live_nicks: set[str] | frozenset[str]) -> set[str]:
    out: set[str] = set()
    for n in live_nicks:
        if not is_worker_nick(n):
            continue
        out.add((canonical_worker_nick(n) or n).lower())
    return out


def review_blocked_for_author(
    row: dict[str, Any],
    nick: str,
    live_nicks: set[str] | frozenset[str],
) -> bool:
    """Block MRB/UAT for author seats (bobiverse#240 / gh-Jeeves#230).

    * Exact author seat: blocked while any other worker seat is live.
    * Same-machine sibling: blocked while another machine has a live seat.
    * Covers ``implementer_seat`` / ``mrb_author_seat`` / legacy ``author_seat``.
    """
    task = str(row.get("task") or "").upper()
    if task not in ("MRB", "UAT"):
        return False
    authors = author_seats_of(row)
    if not authors:
        return False
    me = (canonical_worker_nick(nick) or nick).lower()
    me_p = parse_worker_nick(nick)
    me_mid = (me_p[0].lower() if me_p else "")
    live = _live_worker_nicks(live_nicks)

    for author in authors:
        author_l = author.lower()
        if author_l == me:
            # FR #224 / #226: self-MRB always blocked, even as sole live seat.
            if task == "MRB" or (live - {me}):
                return True
            continue
        author_p = parse_worker_nick(author)
        if author_p and me_p and author_p[0].lower() == me_mid:
            for n in live:
                p = parse_worker_nick(n)
                if p and p[0].lower() != me_mid:
                    return True
    return False


def mrb_blocked_for_author(
    row: dict[str, Any],
    nick: str,
    live_nicks: set[str] | frozenset[str],
) -> bool:
    """Backward-compatible alias for ``review_blocked_for_author``."""
    return review_blocked_for_author(row, nick, live_nicks)


def _norm_pr_id(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if not s.startswith("#"):
        s = f"#{s}"
    return s


def mrb_row_not_offerable(row: dict[str, Any], doc: dict[str, Any] | None = None) -> bool:
    """True when an MRB row must not be assigned (bobiverse#224).

    Covers: ``merged`` flag, mrb-*-fix / fix(mrb-N) titles, and a UAT row for
    the same PR id (merge already superseding).
    """
    if str(row.get("task") or "").upper() != "MRB":
        return False
    if row.get("merged") is True:
        return True
    if str(row.get("action") or "").lower() == "merged":
        return True
    line = str(row.get("line") or "")
    if is_mrb_fix_pr_title(line):
        return True
    if doc is None:
        return False
    pr = _norm_pr_id(row.get("pr_id") or row.get("id"))
    if not pr:
        return False
    repo = str(row.get("repo") or "")
    for bucket in ("unaccepted", "accepted", "done"):
        for r in doc.get(bucket) or []:
            if not isinstance(r, dict):
                continue
            if str(r.get("repo") or "") != repo:
                continue
            if str(r.get("task") or "").upper() != "UAT":
                continue
            uat_pr = _norm_pr_id(r.get("pr_id") or r.get("id"))
            if uat_pr == pr:
                # Any UAT for this PR id means MRB for that PR is stale.
                return True
    return False


def purge_stale_mrb_rows(home: Path) -> int:
    """Drop unaccepted MRB rows that are mrb-fix titles or already merged.

    Returns the number of rows removed. Persists when anything changed.
    """
    doc = load_queue(home)
    before = list(doc.get("unaccepted") or [])
    kept: list[dict[str, Any]] = []
    removed = 0
    for r in before:
        if isinstance(r, dict) and mrb_row_not_offerable(r, doc):
            removed += 1
            log.info(
                "event=purge_stale_mrb repo=%s id=%s line=%s",
                r.get("repo"),
                r.get("id"),
                str(r.get("line") or "")[:80],
            )
            continue
        kept.append(r)
    if removed:
        doc["unaccepted"] = kept
        save_queue(home, doc)
    return removed


def _parse_cooldown_until(until_s: str) -> float | None:
    s = str(until_s or "").strip()
    if not s:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def nick_on_giveup_cooldown(row: dict[str, Any], nick: str, now: float | None = None) -> bool:
    """FR #226: do not re-offer the same row to a nick that just GIVEUP/NACK'd it.

    Per-nick ``giveup_by`` cooldowns leave the row offerable to other seats.
    Row-wide ``cooldown_until`` is legacy (FR #180) and only applies when no
    ``giveup_by`` map is present. ``needs_human`` is checked at the decide loop.
    """
    import time as _time

    me = (canonical_worker_nick(nick) or nick or "").strip().lower()
    if not me:
        return False
    now_f = _time.time() if now is None else float(now)
    giveups = row.get("giveup_by") or {}
    if isinstance(giveups, dict) and giveups:
        entry = giveups.get(me) or giveups.get(str(nick or "").strip().lower())
        if not isinstance(entry, dict):
            return False  # this nick never gave up; other seats remain free
        until = _parse_cooldown_until(str(entry.get("cooldown_until") or ""))
        return until is not None and now_f < until
    # legacy row-wide cooldown_until (no per-nick map)
    until = _parse_cooldown_until(str(row.get("cooldown_until") or ""))
    return until is not None and now_f < until


@dataclass
class AssignDecision:
    action: str  # assign | nothing | skip | busy | open
    line: str | None = None
    reason: str = ""
    row: dict[str, Any] | None = None


@dataclass
class ChairAssignState:
    """Outstanding offers keyed by worker nick; persisted under home/offers.json.

    CAST IRON: at most one open offer per ``row_key`` (repo|task|#n) across all
    workers. ``offered_keys`` skips that job for every other seat until ACK,
    DONE, or ``timeout_s`` (default 90s) elapses with no response.
    """

    timeout_s: float = DEFAULT_OFFER_TIMEOUT_S
    max_offer_attempts: int = DEFAULT_MAX_OFFER_ATTEMPTS
    open: dict[str, dict[str, Any]] = field(default_factory=dict)
    # FR #133: nick|row_key → timed-out offer count without ACK
    attempts: dict[str, int] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    _home: Path | None = None

    def bind(self, home: Path) -> None:
        self._home = Path(home)
        # Apply env override once at bind (service restart / tests set env first).
        self.timeout_s = offer_timeout_s_from_env(self.timeout_s)
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
        if isinstance(doc, dict) and isinstance(doc.get("attempts"), dict):
            self.attempts = {
                str(k): int(v)
                for k, v in doc["attempts"].items()
                if str(k) and int(v) > 0
            }

    def save(self) -> None:
        if self._home is None:
            return
        path = offers_path(self._home)
        tmp = path.with_suffix(".tmp")
        body = json.dumps(
            {"v": 1, "open": self.open, "attempts": self.attempts},
            indent=2,
            sort_keys=True,
        ) + "\n"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)

    def clear(self, nick: str) -> None:
        self.open.pop(nick, None)
        # Successful ACK/DONE: clear attempt counters for this nick
        prefix = f"{nick}|"
        self.attempts = {k: v for k, v in self.attempts.items() if not k.startswith(prefix)}
        self.save()

    def has_open(self, nick: str) -> bool:
        return nick in self.open

    def _attempt_key(self, nick: str, row: dict[str, Any]) -> str:
        return f"{nick}|{row_key(row)}"

    def expire_timed_out(self, now: float | None = None) -> list[str]:
        now_f = time.time() if now is None else float(now)
        expired: list[str] = []
        for nick, ent in list(self.open.items()):
            offered_at = float(ent.get("offered_at") or 0.0)
            if offered_at <= 0 or (now_f - offered_at) >= float(self.timeout_s):
                expired.append(nick)
                row = ent.get("row") if isinstance(ent.get("row"), dict) else None
                if isinstance(row, dict) and row.get("repo"):
                    ak = self._attempt_key(nick, row)
                    self.attempts[ak] = int(self.attempts.get(ak) or 0) + 1
                    log.info(
                        "event=offer_timeout nick=%s job=%s attempts=%s",
                        nick,
                        row_key(row),
                        self.attempts[ak],
                    )
                self.open.pop(nick, None)
        if expired:
            self.save()
            for n in expired:
                if not any(
                    True
                    for k in self.attempts
                    if k.startswith(f"{n}|")
                ):
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
        pr_exists: Callable[[str, str], bool] | None = None,
    ) -> AssignDecision:
        self._home = Path(home)
        self.expire_timed_out(now=now)
        if not is_worker_nick(nick):
            return AssignDecision("skip", reason="not_worker")
        if not nick_matches_shop(nick, channel):
            return AssignDecision("skip", reason="wrong_shop")
        canon = canonical_worker_nick(nick) or nick
        q = load_queue(home)
        has_accepted = any(
            str(row.get("nick") or "") == nick
            for row in (q.get("accepted") or [])
            if isinstance(row, dict)
        )
        # Busy / already accepted → do not assign another.
        # Ghost busy: digest/queue workers say busy but no accepted row
        # (missed DONE during chair restart, or resync dropped finished job
        # without clearing workers). Heal so !bored can take the next job.
        if worker_state(home, nick) == "busy":
            if has_accepted:
                return AssignDecision("busy", reason="busy")
            from .queue import set_worker_state

            set_worker_state(home, nick, "idle")
            log.warning(
                "event=busy_ghost_heal nick=%s reason=no_accepted",
                nick,
            )
        elif has_accepted:
            return AssignDecision("busy", reason="accepted")
        # FR #182: if this seat already has an open offer, rebroadcast that line.
        # Silent skip made workers think assign was broken when they missed the first PRIVMSG.
        open_ent = None
        if self.has_open(canon):
            open_ent = self.open.get(canon)
        elif self.has_open(nick):
            open_ent = self.open.get(nick)
        if isinstance(open_ent, dict):
            line = str(open_ent.get("line") or "").strip()
            row = open_ent.get("row") if isinstance(open_ent.get("row"), dict) else None
            if not line and isinstance(row, dict):
                line = format_assign_line(
                    nick, row, chair_nick=chair_nick, channel=channel
                )
            if line:
                return AssignDecision(
                    "open",
                    line=line,
                    reason="one_open_offer",
                    row=dict(row) if isinstance(row, dict) else None,
                )
            return AssignDecision("open", reason="one_open_offer")

        live = set(live_nicks or ())
        # bobiverse#224: drop stale merged / mrb-*-fix MRB rows before pick.
        purge_stale_mrb_rows(home)
        q = load_queue(home)
        offered = self.offered_keys()
        accepted_keys = {
            row_key(r)
            for r in (q.get("accepted") or [])
            if isinstance(r, dict)
        }
        pick: dict[str, Any] | None = None
        purge_keys: list[str] = []
        for row in ordered_unaccepted(home):
            if not isinstance(row, dict):
                continue
            key = row_key(row)
            if key in accepted_keys:
                continue
            if key in offered:
                continue
            # FR #709: never offer closed / skip-label / harvest / already-DONE FRs.
            skip = row_skip_fr_reason(row)
            if skip:
                log.info(
                    "event=offer_skip_fr nick=%s job=%s reason=%s",
                    canon,
                    key,
                    skip,
                )
                purge_keys.append(key)
                continue
            if str(row.get("task") or "").upper() in ("FR", "PR") and fr_already_done(
                q, str(row.get("repo") or ""), str(row.get("id") or "")
            ):
                log.info(
                    "event=offer_skip_fr nick=%s job=%s reason=already_done",
                    canon,
                    key,
                )
                purge_keys.append(key)
                continue
            # bobiverse#168: chair/outbox jobs only to the capable machine (ionos).
            if row_blocked_for_machine(row, nick):
                continue
            if review_blocked_for_author(row, nick, live):
                continue
            if nick_on_giveup_cooldown(row, nick, now):
                continue
            if row.get("needs_human") in (True, "true", "1", 1):
                continue
            # bobiverse#224: skip merged / mrb-*-fix / UAT-superseded MRB rows.
            if mrb_row_not_offerable(row, q):
                log.info(
                    "event=offer_skip_stale_mrb nick=%s job=%s line=%s",
                    canon,
                    key,
                    str(row.get("line") or "")[:80],
                )
                continue
            # bobiverse#247: skip MRB rows without a real pull URL (or PR 404).
            if not mrb_row_offerable(row, pr_exists=pr_exists):
                log.info(
                    "event=offer_skip_mrb_no_pr nick=%s job=%s url=%s",
                    canon,
                    key,
                    str(row.get("url") or "")[:120],
                )
                continue
            # FR #133: skip jobs this seat was offered N times with no ACK.
            ak = self._attempt_key(canon, row)
            if int(self.attempts.get(ak) or 0) >= int(self.max_offer_attempts):
                log.info(
                    "event=offer_skip_attempts nick=%s job=%s attempts=%s",
                    canon,
                    key,
                    self.attempts.get(ak),
                )
                continue
            pick = dict(row)
            break
        if purge_keys:
            before = list(q.get("unaccepted") or [])
            q["unaccepted"] = [
                r
                for r in before
                if not isinstance(r, dict) or row_key(r) not in set(purge_keys)
            ]
            if len(q["unaccepted"]) != len(before):
                save_queue(home, q)
        if pick is None:
            # Distinguish empty vs all blocked (FR #141: strict focus with no
            # matching focused jobs also yields nothing queued).
            if not ordered_unaccepted(home):
                line = format_nothing_queued(nick)
                return AssignDecision("nothing", line=line, reason="empty")
            line = format_nothing_queued(nick)
            return AssignDecision("nothing", line=line, reason="none_eligible")

        # Canonicalise legacy PR → FR for ACK match (#102)
        if str(pick.get("task") or "").upper() == "PR":
            pick["task"] = "FR"
        # Stamp resolved pull URL onto MRB rows so the wire line never invents one.
        resolved = resolve_assign_url(pick)
        if resolved:
            pick["url"] = resolved
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


def github_pr_exists_checker(
    *,
    home: Path | None = None,
    cache: dict[str, bool] | None = None,
) -> Callable[[str, str], bool] | None:
    """Return a ``pr_exists(repo, num)`` callback when a GitHub token is available.

    Used by the chair !bored path (bobiverse#247) so synthetic ``/pull/N`` rows
    that 404 are skipped. Returns None when offline / no token (structural
    URL checks in ``mrb_row_offerable`` still apply).
    """
    try:
        from .resync import load_github_token
    except Exception:  # noqa: BLE001
        return None
    homes = [home] if home is not None else None
    token = load_github_token(homes=homes)
    if not token:
        return None
    store: dict[str, bool] = cache if cache is not None else {}

    def _check(repo: str, num: str) -> bool:
        key = f"{repo}#{num}"
        if key in store:
            return store[key]
        import urllib.error
        import urllib.request

        url = f"https://api.github.com/repos/{repo}/pulls/{num}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "gh-Jeeves-assign",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                ok = 200 <= int(getattr(resp, "status", 200) or 200) < 300
        except urllib.error.HTTPError as e:
            ok = False
            if int(getattr(e, "code", 0) or 0) not in (404, 410):
                log.warning(
                    "event=mrb_pr_exists_http repo=%s num=%s code=%s",
                    repo,
                    num,
                    getattr(e, "code", "?"),
                )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "event=mrb_pr_exists_err repo=%s num=%s err=%s",
                repo,
                num,
                type(e).__name__,
            )
            ok = False
        store[key] = ok
        return ok

    return _check


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
