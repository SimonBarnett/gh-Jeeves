"""!list / !help PM lines (FR #50 / agentic_irc #208). Channel stays silent."""

from __future__ import annotations

import time
from pathlib import Path

from .focus import focus_label_for_repo, sort_unaccepted_rows
from .ignore import filter_rows_not_ignored
from .queue import load_queue

# Soft page size when not listing all; never silent — always emit +M more when truncated.
LIST_PAGE_DEFAULT = 30
LIST_LINE_MAX = 400
LIST_RATE_S = 30.0
FLOOD_S = 0.8

_HELP_LINES = (
    "Jeeves: !list [all|<repo>|fr|mrb|uat] — queue by PM (type in channel; answer is PM)",
    "Jeeves: !help — this help by PM",
    "Workers: ACK/DONE/NACK in #{machine}; ears own !bored/OFFER",
    "Busy/idle lives on digest webhook only (not IRC status talk)",
)

_list_last: dict[str, float] = {}


def list_rate_ok(nick: str, *, now: float | None = None) -> bool:
    t = float(now if now is not None else time.time())
    key = (nick or "").strip().lower()
    last = _list_last.get(key)
    if last is not None and (t - last) < LIST_RATE_S:
        return False
    _list_last[key] = t
    return True


def list_rate_notice(nick: str, *, now: float | None = None) -> str:
    t = float(now if now is not None else time.time())
    key = (nick or "").strip().lower()
    last = _list_last.get(key) or t
    left = max(0, int(LIST_RATE_S - (t - last)))
    return f"rate limited; try again in {left}s"


def reset_list_rate() -> None:
    _list_last.clear()


def _norm_id(ident: str) -> str:
    s = str(ident or "").strip()
    if not s:
        return "?"
    return s if s.startswith("#") else f"#{s}"


def _clip_utf8(s: str, max_b: int) -> str:
    if max_b < 1:
        return ""
    raw = s or ""
    if len(raw.encode("utf-8")) <= max_b:
        return raw
    # leave room for ellipsis
    out = raw
    while out and len(out.encode("utf-8")) > max_b - 1:
        out = out[:-1]
    return out + "…" if out != raw else out


def format_list_line(
    row: dict,
    *,
    line_max: int = LIST_LINE_MAX,
    index: int | None = None,
    focus_tag: str | None = None,
) -> str:
    """
    Wire format (FR #50): ``<MODE> <owner/repo>#<n> <title>``
    MODE is FR|MRB|UAT (task). Optional leading ``N.`` for multi-line lists.
    FR #68: optional ``[high|medium|low|n]`` after MODE when focused by name/number.
    """
    task = str(row.get("task") or "?").upper()
    repo = str(row.get("repo") or "?").strip()
    ident = _norm_id(str(row.get("id") or ""))
    # ensure single # between repo and n
    num = ident.lstrip("#")
    ref = f"{repo}#{num}"
    title = str(row.get("line") or row.get("title") or "").replace("\n", " ").strip()
    prefix = f"{index}. " if index is not None else ""
    tag = f" [{focus_tag}]" if focus_tag else ""
    head = f"{prefix}{task}{tag} {ref}"
    budget = max(8, int(line_max) - len(head.encode("utf-8")) - (1 if title else 0))
    if title:
        title = _clip_utf8(title, budget)
        line = f"{head} {title}"
    else:
        line = head
    return _clip_utf8(line, line_max)


def format_unaccepted_list(
    home: Path,
    *,
    task_filter: str | None = None,
    repo_filter: str | None = None,
    list_all: bool = False,
    max_lines: int | None = None,
    line_max: int = LIST_LINE_MAX,
    now: float | None = None,
) -> list[str]:
    """
    Build PM lines for !list.

    - Default: unaccepted only, up to LIST_PAGE_DEFAULT, then explicit ``+M more``.
    - ``list_all``: unaccepted + accepted (busy), all rows paced by caller (no silent cap).
    - Never truncates without a trailing more-hint when jobs remain.
    """
    del now  # reserved for age display if re-added
    doc = load_queue(home)
    unacc = list(doc.get("unaccepted") or [])
    acc = list(doc.get("accepted") or [])

    if list_all:
        # mark accepted for clarity in title if missing
        rows: list[dict] = []
        for r in unacc:
            rows.append(dict(r))
        for r in acc:
            row = dict(r)
            if not str(row.get("line") or "").startswith("[accepted]"):
                row["line"] = f"[accepted] {row.get('line') or row.get('title') or ''}".strip()
            rows.append(row)
    else:
        rows = unacc

    # FR #75: ignored repos never appear in !list regardless of focus priority.
    rows = filter_rows_not_ignored(home, rows)
    # FR #68: one sort for !list and !bored
    rows = sort_unaccepted_rows(home, rows)

    if task_filter:
        tf = task_filter.upper()
        rows = [r for r in rows if str(r.get("task") or "").upper() == tf]
    if repo_filter:
        rf = repo_filter.lower().strip()
        rows = [r for r in rows if rf in str(r.get("repo") or "").lower()]

    if not rows:
        return ["queue empty"]

    total = len(rows)
    if list_all:
        cap = total  # no silent cap — send all (caller paces)
    else:
        cap = max(1, int(max_lines if max_lines is not None else LIST_PAGE_DEFAULT))
        cap = min(cap, total)

    show = rows[:cap]
    out: list[str] = []
    if total > 1 or (not list_all and total > len(show)):
        kind = "jobs" if list_all else "unaccepted"
        out.append(f"{total} {kind} (showing {len(show)})")
    for i, row in enumerate(show, start=1):
        tag = focus_label_for_repo(home, str(row.get("repo") or ""))
        out.append(
            format_list_line(
                row,
                line_max=line_max,
                index=i if total > 1 else None,
                focus_tag=tag,
            )
        )
    more = total - len(show)
    if more > 0:
        hint = "!list all" if not list_all else "!list <repo> to filter"
        if repo_filter:
            hint = f"!list all {repo_filter}" if not list_all else hint
        out.append(f"... and {more} more; {hint}")
    return out


def help_lines() -> list[str]:
    return list(_HELP_LINES)
