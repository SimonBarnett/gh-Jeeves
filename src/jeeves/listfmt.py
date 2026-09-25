"""!list / !help PM lines (FR #39 / agentic_irc #208). Channel stays silent."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .queue import load_queue

LIST_MAX_LINES = 10
LIST_LINE_MAX = 400
LIST_RATE_S = 30.0

_HELP_LINES = (
    "Jeeves: !list [fr|mrb|uat|all] — queue by PM (type in channel; answer is PM)",
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


def _age(row: dict, now: float | None = None) -> str:
    t = float(now if now is not None else time.time())
    ts = str(row.get("ts") or row.get("created_at") or "")
    if not ts:
        return "?"
    try:
        # accept epoch ms or ISO-ish
        if ts.isdigit():
            sec = t - (int(ts) / 1000.0 if len(ts) > 11 else int(ts))
        else:
            # rough: treat as missing
            return "?"
        sec = max(0, int(sec))
    except (TypeError, ValueError):
        return "?"
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def format_list_line(index: int, row: dict, *, line_max: int = LIST_LINE_MAX, now: float | None = None) -> str:
    task = str(row.get("task") or "?")
    repo = str(row.get("repo") or "?")
    ident = str(row.get("id") or "?")
    age = _age(row, now=now)
    title = str(row.get("line") or row.get("title") or "").replace("\n", " ").strip()
    head = f"#{index} {task} {repo}{ident} {age}"
    budget = max(8, int(line_max) - len(head.encode("utf-8")) - 1)
    if title:
        while title and len(title.encode("utf-8")) > budget - 1:
            title = title[:-1]
        if title and len(str(row.get("line") or "").encode("utf-8")) > budget:
            title = title + "…"
        line = f"{head} {title}"
    else:
        line = head
    while len(line.encode("utf-8")) > line_max and len(line) > 1:
        line = line[:-2] + "…"
    return line


def format_unaccepted_list(
    home: Path,
    *,
    task_filter: str | None = None,
    repo_filter: str | None = None,
    list_all: bool = False,
    max_lines: int = LIST_MAX_LINES,
    line_max: int = LIST_LINE_MAX,
    now: float | None = None,
) -> list[str]:
    doc = load_queue(home)
    rows = list(doc.get("unaccepted") or [])
    rows.sort(key=lambda r: int(r.get("seq") or 0))
    if task_filter:
        tf = task_filter.upper()
        rows = [r for r in rows if str(r.get("task") or "").upper() == tf]
    if repo_filter:
        rf = repo_filter.lower()
        rows = [r for r in rows if rf in str(r.get("repo") or "").lower()]
    if not rows:
        return ["queue empty"]
    total = len(rows)
    cap = max(1, int(max_lines))
    if list_all:
        cap = max(cap, total)
    show = rows[:cap]
    out: list[str] = []
    if total > 1 or total > len(show):
        out.append(f"{total} unaccepted (showing {len(show)})")
    for i, row in enumerate(show, start=1):
        out.append(format_list_line(i, row, line_max=line_max, now=now))
    more = total - len(show)
    if more > 0:
        out.append(f"+{more} more; !list all")
    return out


def help_lines() -> list[str]:
    return list(_HELP_LINES)
