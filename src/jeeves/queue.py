"""Queue engine: unaccepted / accepted / done + supersede (scripts only)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUEUE_VERSION = 1
ACCEPTED_CAP = 200
DONE_CAP = 200

# FR #102: workers with ts older than this are expired (QUIT or unseen).
DEFAULT_STALE_WORKER_S = 1800.0

# agentic_irc #207-compatible closes grammar (Closes/Fixes/Resolves/Refs #n).
_CLOSES_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)\b",
)

# Log-once set for no-match ACK reasons (cleared in tests).
_ack_no_match_logged: set[str] = set()


def tasks_equivalent(a: str, b: str) -> bool:
    """FR #102: legacy issue rows queued as PR match an ACK FR (and vice versa)."""
    x = str(a or "").upper()
    y = str(b or "").upper()
    if x == y:
        return True
    return {x, y} <= {"FR", "PR"}


@dataclass(frozen=True)
class Claim:
    repo: str
    task: str  # FR | MRB | UAT | CLOSE | RESTORE_FR
    id: str  # #n (issue or PR number for the claim row)
    event: str = ""
    action: str = ""
    line: str = ""
    url: str = ""
    refs: tuple[str, ...] = ()  # linked issue ids e.g. ("#19",) when PR supersedes FR
    pr_id: str = ""  # pull request #n when claim is about a PR (MRB/UAT/RESTORE)
    merged: bool | None = None

    @property
    def key(self) -> str:
        return f"{self.repo}|{self.task}|{self.id}"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def empty_queue() -> dict[str, Any]:
    return {"v": QUEUE_VERSION, "unaccepted": [], "accepted": [], "done": [], "workers": {}}


def queue_path(home: Path) -> Path:
    return Path(home) / "queue.json"


def _migrate_legacy_pr_tasks(doc: dict[str, Any]) -> bool:
    """FR #102: early issues queued as task=PR → FR so ACK FR can match."""
    changed = False
    for bucket in ("unaccepted", "accepted", "done"):
        rows = doc.get(bucket)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("task") or "").upper() == "PR":
                row["task"] = "FR"
                changed = True
    return changed


def load_queue(home: Path) -> dict[str, Any]:
    path = queue_path(home)
    if not path.is_file():
        return empty_queue()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_queue()
    if not isinstance(doc, dict):
        return empty_queue()
    doc.setdefault("v", QUEUE_VERSION)
    for k in ("unaccepted", "accepted", "done"):
        if not isinstance(doc.get(k), list):
            doc[k] = []
    if not isinstance(doc.get("workers"), dict):
        doc["workers"] = {}
    if _migrate_legacy_pr_tasks(doc):
        try:
            save_queue(home, doc)
        except OSError:
            pass
    return doc


def save_queue(home: Path, doc: dict[str, Any]) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = queue_path(home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_closes_issue_ids(*texts: str) -> tuple[str, ...]:
    found: list[str] = []
    for t in texts:
        if not t:
            continue
        for m in _CLOSES_RE.finditer(t):
            found.append(f"#{m.group(1)}")
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for i in found:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return tuple(out)


def claim_from_payload(event: str, payload: dict) -> Claim | None:
    """Map GitHub webhook event → queue claim (FR/MRB/UAT). K4 / #207 supersede fields."""
    repo_obj = payload.get("repository") or {}
    full = repo_obj.get("full_name") or ""
    if not full and repo_obj.get("name"):
        owner = (repo_obj.get("owner") or {}).get("login") or "unknown"
        full = f"{owner}/{repo_obj['name']}"
    if not full:
        return None
    action = str(payload.get("action") or "")
    event = (event or "").lower()

    if event == "issues":
        issue = payload.get("issue") or {}
        num = issue.get("number")
        if num is None:
            return None
        ident = f"#{int(num)}"
        title = str(issue.get("title") or "")[:120]
        body = str(issue.get("body") or "")
        url = str(issue.get("html_url") or "")
        if action in ("opened", "reopened"):
            return Claim(
                repo=full,
                task="FR",
                id=ident,
                event=event,
                action=action,
                line=title,
                url=url,
            )
        if action == "closed":
            return Claim(
                repo=full,
                task="CLOSE",
                id=ident,
                event=event,
                action=action,
                line=title,
                url=url,
            )
        return None

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        num = pr.get("number")
        if num is None:
            return None
        ident = f"#{int(num)}"
        title = str(pr.get("title") or "")[:120]
        url = str(pr.get("html_url") or "")
        body = str(pr.get("body") or "")
        merged = bool(pr.get("merged"))
        refs = extract_closes_issue_ids(title, body)
        # Keep closes text in line so apply_queue_event can re-extract if needed.
        line = title
        if refs:
            line = f"{title} " + " ".join(f"Closes {r}" for r in refs)
        if action in ("opened", "ready_for_review", "reopened", "synchronize"):
            return Claim(
                repo=full,
                task="MRB",
                id=ident,
                event=event,
                action=action,
                line=line,
                url=url,
                refs=refs,
                pr_id=ident,
                merged=None,
            )
        if action == "closed" and merged:
            # K4: MRB PASS → drop MRB (pr_id), UAT for each linked FR (or PR id if none).
            uat_id = refs[0] if refs else ident
            return Claim(
                repo=full,
                task="UAT",
                id=uat_id,
                event=event,
                action="merged",
                line=line,
                url=url,
                refs=refs if refs else (ident,),
                pr_id=ident,
                merged=True,
            )
        if action == "closed" and not merged:
            return Claim(
                repo=full,
                task="RESTORE_FR",
                id=ident,
                event=event,
                action=action,
                line=line,
                url=url,
                refs=refs,
                pr_id=ident,
                merged=False,
            )
        return None

    return None


def _same(row: dict, repo: str, task: str, ident: str) -> bool:
    return (
        str(row.get("repo") or "") == repo
        and str(row.get("task") or "") == task
        and str(row.get("id") or "") == ident
    )


def _remove_matching(rows: list[dict], pred) -> int:
    before = len(rows)
    rows[:] = [r for r in rows if not pred(r)]
    return before - len(rows)


def _append_unaccepted(doc: dict, claim: Claim, **extra: Any) -> None:
    row = {
        "repo": claim.repo,
        "task": claim.task,
        "id": claim.id,
        "ts": _utc_now(),
        "line": claim.line,
        "event": claim.event,
        "action": claim.action,
        "url": claim.url,
        "seq": int(time.time() * 1000),
    }
    if claim.refs:
        row["refs"] = list(claim.refs)
    if claim.pr_id:
        row["pr_id"] = claim.pr_id
    if claim.merged is not None:
        row["merged"] = claim.merged
    row.update({k: v for k, v in extra.items() if v is not None})
    # de-dupe same key
    _remove_matching(
        doc["unaccepted"],
        lambda r: _same(r, claim.repo, claim.task, claim.id),
    )
    doc["unaccepted"].append(row)


def _linked_ids(claim: Claim) -> tuple[str, ...]:
    refs = claim.refs or extract_closes_issue_ids(claim.line)
    return tuple(refs)


def _remove_tasks_for_ids(
    doc: dict,
    repo: str,
    idents: tuple[str, ...],
    tasks: set[str],
    *,
    buckets: tuple[str, ...] = ("unaccepted", "accepted"),
) -> int:
    n = 0
    idset = {_norm_ident(i) for i in idents if i}
    taskset = {t.upper() for t in tasks}
    for bucket in buckets:
        n += _remove_matching(
            doc[bucket],
            lambda r, _repo=repo, _ids=idset, _ts=taskset: (
                str(r.get("repo") or "") == _repo
                and str(r.get("task") or "").upper() in _ts
                and _norm_ident(str(r.get("id") or "")) in _ids
            ),
        )
        # also drop MRB rows whose pr_id matches
        n += _remove_matching(
            doc[bucket],
            lambda r, _repo=repo, _ids=idset, _ts=taskset: (
                str(r.get("repo") or "") == _repo
                and str(r.get("task") or "").upper() in _ts
                and _norm_ident(str(r.get("pr_id") or "")) in _ids
            ),
        )
    return n


def apply_queue_event(home: Path, claim: Claim) -> str:
    """Apply supersede rules (K4 / agentic_irc #207); return action tag."""
    # FR #75: ignored repos never enqueue or supersede.
    from .ignore import is_ignored

    if is_ignored(home, claim.repo):
        return "ignored"
    doc = load_queue(home)
    repo, task, ident = claim.repo, claim.task, _norm_ident(claim.id)
    pr_id = _norm_ident(claim.pr_id or (ident if task in ("MRB", "RESTORE_FR") else ""))
    links = _linked_ids(claim)

    if task == "CLOSE":
        # Issue closed: drop FR/MRB/UAT for this issue id (and PR rows that only tracked it).
        n = _remove_tasks_for_ids(doc, repo, (ident,), {"FR", "MRB", "UAT", "PR", "FIX"})
        save_queue(home, doc)
        return f"removed:{n}"

    if task == "FR":
        # Reopened/opened FR: drop stale UAT/PR for same id; enqueue FR (idempotent).
        _remove_tasks_for_ids(doc, repo, (ident,), {"UAT", "PR"})
        # de-dupe prior FR same id
        _remove_matching(
            doc["unaccepted"],
            lambda r: str(r.get("repo")) == repo
            and _norm_ident(str(r.get("id") or "")) == ident
            and str(r.get("task") or "").upper() == "FR",
        )
        _append_unaccepted(doc, claim)
        save_queue(home, doc)
        return "enqueued:FR"

    if task == "MRB":
        # PR opened: supersede linked FRs (and UAT leftovers); enqueue MRB once.
        for fr in links:
            _remove_tasks_for_ids(doc, repo, (fr,), {"FR", "PR", "UAT"})
        # drop prior MRB same PR id
        _remove_tasks_for_ids(doc, repo, (ident, pr_id or ident), {"MRB"})
        _append_unaccepted(doc, claim)
        save_queue(home, doc)
        # FR #113: carry item !focus from linked FR → this MRB
        try:
            from .focus import retarget_item_focus

            for fr in links:
                retarget_item_focus(home, repo, fr, new_id=ident)
        except Exception:
            pass
        return "enqueued:MRB"

    if task == "UAT":
        # K4: merged PR — remove the MRB row for this PR (not just FR id), then UAT linked FRs.
        pr = pr_id or ident
        _remove_tasks_for_ids(doc, repo, (pr,), {"MRB"})
        # drop accepted MRB for this PR so workers don't stay on merged work
        _remove_matching(
            doc["accepted"],
            lambda r: str(r.get("repo")) == repo
            and str(r.get("task") or "").upper() == "MRB"
            and (
                _norm_ident(str(r.get("id") or "")) == pr
                or _norm_ident(str(r.get("pr_id") or "")) == pr
            ),
        )
        uat_targets = links if links else (ident,)
        for fr in uat_targets:
            _remove_tasks_for_ids(doc, repo, (fr,), {"FR", "UAT", "PR"})
            _append_unaccepted(
                doc,
                Claim(
                    repo=repo,
                    task="UAT",
                    id=_norm_ident(fr),
                    event=claim.event,
                    action="merged",
                    line=claim.line,
                    url=claim.url,
                    refs=(_norm_ident(fr),),
                    pr_id=pr,
                    merged=True,
                ),
            )
        save_queue(home, doc)
        return "enqueued:UAT"

    if task == "RESTORE_FR":
        # PR closed unmerged: drop MRB for this PR; restore linked FRs.
        pr = pr_id or ident
        _remove_tasks_for_ids(doc, repo, (pr,), {"MRB"})
        _remove_matching(
            doc["accepted"],
            lambda r: str(r.get("repo")) == repo
            and str(r.get("task") or "").upper() == "MRB"
            and (
                _norm_ident(str(r.get("id") or "")) == pr
                or _norm_ident(str(r.get("pr_id") or "")) == pr
            ),
        )
        restores = links if links else ()
        for fr in restores:
            _append_unaccepted(
                doc,
                Claim(
                    repo=repo,
                    task="FR",
                    id=_norm_ident(fr),
                    event=claim.event,
                    action="restore",
                    line=claim.line,
                    url=claim.url,
                ),
            )
        save_queue(home, doc)
        return "restored:FR"

    _append_unaccepted(doc, claim)
    save_queue(home, doc)
    return f"enqueued:{task}"


def unaccepted_tasks(home: Path) -> list[dict]:
    return list(load_queue(home).get("unaccepted") or [])


def ordered_unaccepted(home: Path) -> list[dict]:
    """Unaccepted rows: ignore filter + FR #68 focus sort (same as !list / !bored)."""
    from .focus import sort_unaccepted_rows
    from .ignore import filter_rows_not_ignored

    doc = load_queue(home)
    rows = list(doc.get("unaccepted") or [])
    rows = filter_rows_not_ignored(home, rows)
    return sort_unaccepted_rows(home, rows)


def top_unaccepted(home: Path) -> dict | None:
    """Top unaccepted job for ear !bored offers. Skips ignored; focus-first (#68)."""
    rows = ordered_unaccepted(home)
    return rows[0] if rows else None


def format_offer(nick: str, row: dict) -> str:
    """K11: single-line OFFER only (ear-owned). See jeeves.offer."""
    from .offer import format_offer as _fmt

    return _fmt(nick, row)


def _norm_ident(ident: str) -> str:
    s = str(ident or "").strip()
    if not s:
        return ""
    return s if s.startswith("#") else f"#{s}"


def _norm_repo(repo: str) -> str:
    return str(repo or "").strip()


def find_unaccepted(doc: dict, task: str, repo: str, ident: str) -> dict | None:
    """Locate unaccepted row (case-insensitive task; #n normalized; FR↔PR)."""
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    for row in doc.get("unaccepted") or []:
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            return row
    return None


def _mark_worker_busy(
    doc: dict[str, Any],
    nick_s: str,
    *,
    repo: str,
    task_u: str,
    ident: str,
    channel_s: str,
) -> None:
    doc.setdefault("workers", {})[nick_s] = {
        "state": "busy",
        "job": f"{repo} {task_u} {ident}",
        "channel": channel_s,
        "ts": _utc_now(),
    }


def _same_machine_nicks(a: str, b: str) -> bool:
    from .nicks import parse_worker_nick

    pa = parse_worker_nick(a)
    pb = parse_worker_nick(b)
    return bool(pa and pb and pa[0] == pb[0])


def accept_job(
    home: Path,
    nick: str,
    channel: str,
    task: str,
    repo: str,
    ident: str,
) -> tuple[str, dict | None]:
    """K3 / FR #4 / FR #102: ACK path — unaccepted → accepted; worker always busy.

    Idempotent: if already accepted by the same nick, return accepted again.
    Legacy PR-typed issue rows match ACK FR. Same-machine seat restart reassigns.
    Parseable ACK with no queue row still records the worker busy (no_match).
    """
    doc = load_queue(home)
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    if task_u == "PR":
        task_u = "FR"
    nick_s = str(nick or "").strip()
    channel_s = str(channel or "").strip()

    def _busy() -> None:
        _mark_worker_busy(
            doc, nick_s, repo=repo, task_u=task_u, ident=ident, channel_s=channel_s
        )

    # Already accepted by this nick?
    for row in list(doc.get("accepted") or []):
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
            and str(row.get("nick") or "") == nick_s
        ):
            row["task"] = "FR" if tasks_equivalent(row.get("task"), "FR") else str(row.get("task") or task_u).upper()
            if str(row.get("task") or "").upper() == "PR":
                row["task"] = "FR"
            _busy()
            save_queue(home, doc)
            return "accepted", row

    # Same-machine re-ACK: reassign accepted row held by a prior seat pid.
    for row in list(doc.get("accepted") or []):
        holder = str(row.get("nick") or "")
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
            and holder
            and holder != nick_s
            and _same_machine_nicks(holder, nick_s)
        ):
            row["nick"] = nick_s
            row["channel"] = channel_s
            row["accepted_ts"] = _utc_now()
            if str(row.get("task") or "").upper() == "PR":
                row["task"] = "FR"
            # Drop departed holder from workers.
            doc.setdefault("workers", {}).pop(holder, None)
            _busy()
            save_queue(home, doc)
            return "accepted", row

    match = None
    for i, row in enumerate(list(doc["unaccepted"])):
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            match = doc["unaccepted"].pop(i)
            break
    if match is None:
        _busy()
        save_queue(home, doc)
        key = f"{nick_s}|{repo}|{task_u}|{ident}|no_queue_row"
        if key not in _ack_no_match_logged:
            _ack_no_match_logged.add(key)
            import logging

            logging.getLogger("jeeves.queue").info(
                "ack_no_match nick=%s repo=%s task=%s id=%s reason=no_queue_row",
                nick_s,
                repo,
                task_u,
                ident,
            )
        return "no_match", None

    match["nick"] = nick_s
    match["channel"] = channel_s
    match["accepted_ts"] = _utc_now()
    match["task"] = "FR" if tasks_equivalent(match.get("task"), "FR") or task_u == "FR" else task_u
    if str(match.get("task") or "").upper() == "PR":
        match["task"] = "FR"
    match["id"] = ident
    match["repo"] = repo
    doc["accepted"].append(match)
    if len(doc["accepted"]) > ACCEPTED_CAP:
        doc["accepted"] = doc["accepted"][-ACCEPTED_CAP:]
    _busy()
    save_queue(home, doc)
    return "accepted", match


def nack_job(
    home: Path,
    nick: str,
    task: str,
    repo: str,
    ident: str,
) -> tuple[str, dict | None]:
    """NACK|GIVEUP: accepted → unaccepted; worker idle."""
    doc = load_queue(home)
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    nick_s = str(nick or "").strip()
    match = None
    for i, row in enumerate(list(doc["accepted"])):
        if (
            str(row.get("nick") or "") == nick_s
            and _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            match = doc["accepted"].pop(i)
            break
    if match is None:
        doc["workers"][nick_s] = {"state": "idle", "ts": _utc_now()}
        save_queue(home, doc)
        return "no_match", None
    for k in ("nick", "channel", "accepted_ts"):
        match.pop(k, None)
    match["nack_ts"] = _utc_now()
    _remove_matching(
        doc["unaccepted"],
        lambda r: _same(r, repo, str(match.get("task") or task_u), ident),
    )
    doc["unaccepted"].append(match)
    doc["workers"][nick_s] = {"state": "idle", "ts": _utc_now()}
    save_queue(home, doc)
    return "nacked", match


def queue_counts(home: Path) -> dict[str, int]:
    doc = load_queue(home)
    return {
        "unaccepted": len(doc.get("unaccepted") or []),
        "accepted": len(doc.get("accepted") or []),
        "done": len(doc.get("done") or []),
    }


def accepted_rows(home: Path) -> list[dict]:
    return list(load_queue(home).get("accepted") or [])

def complete_job(home: Path, nick: str, task: str, repo: str, ident: str, result: str = "ok", url: str = "") -> tuple[str, dict | None]:
    """DONE path: accepted → done; worker idle; optional supersede hook point."""
    doc = load_queue(home)
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    nick_s = str(nick or "").strip()
    match = None
    for i, row in enumerate(list(doc["accepted"])):
        if (
            str(row.get("nick") or "") == nick_s
            and _norm_repo(str(row.get("repo") or "")) == repo
            and tasks_equivalent(str(row.get("task") or ""), task_u)
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            match = doc["accepted"].pop(i)
            break
    if match is None:
        # still allow DONE to clear busy if row missing
        doc["workers"][nick_s] = {"state": "idle", "ts": _utc_now()}
        save_queue(home, doc)
        return "no_match", None
    match["done_ts"] = _utc_now()
    match["result"] = result
    if url:
        match["done_url"] = url
    doc["done"].append(match)
    if len(doc["done"]) > DONE_CAP:
        doc["done"] = doc["done"][-DONE_CAP:]
    doc["workers"][nick_s] = {"state": "idle", "ts": _utc_now()}
    save_queue(home, doc)
    return "done", match


def set_worker_state(home: Path, nick: str, state: str, **extra: Any) -> None:
    doc = load_queue(home)
    ent = {"state": state, "ts": _utc_now()}
    ent.update(extra)
    doc["workers"][nick] = ent
    save_queue(home, doc)


def worker_state(home: Path, nick: str) -> str:
    doc = load_queue(home)
    w = doc.get("workers") or {}
    ent = w.get(nick) or {}
    return str(ent.get("state") or "idle")


def _parse_worker_ts(raw: Any) -> float | None:
    """Parse ISO ts or epoch float into epoch seconds."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        # 2026-09-25T18:44:47Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        from datetime import datetime

        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def release_worker(
    home: Path,
    nick: str,
    *,
    reason: str = "stale",
) -> list[dict[str, Any]]:
    """FR #102: drop nick from workers; return its accepted rows to unaccepted."""
    doc = load_queue(home)
    nick_s = str(nick or "").strip()
    if not nick_s:
        return []
    released: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for row in list(doc.get("accepted") or []):
        if str(row.get("nick") or "") == nick_s:
            for k in ("nick", "channel", "accepted_ts"):
                row.pop(k, None)
            row["release_ts"] = _utc_now()
            row["release_reason"] = reason
            released.append(row)
            doc.setdefault("unaccepted", []).append(row)
        else:
            kept.append(row)
    doc["accepted"] = kept
    doc.setdefault("workers", {}).pop(nick_s, None)
    save_queue(home, doc)
    return released


def expire_stale_workers(
    home: Path,
    *,
    idle_s: float | None = None,
    max_age_s: float | None = None,
    now: float | None = None,
) -> list[str]:
    """Remove workers whose ts is older than idle_s; release their accepted jobs.

    ``max_age_s`` is an alias for ``idle_s`` (FR #102 tests / callers).
    """
    import os

    ttl = idle_s if idle_s is not None else max_age_s
    if ttl is None:
        try:
            ttl = float(os.environ.get("JEEVES_WORKER_STALE_S") or DEFAULT_STALE_WORKER_S)
        except (TypeError, ValueError):
            ttl = float(DEFAULT_STALE_WORKER_S)
    doc = load_queue(home)
    now_f = time.time() if now is None else float(now)
    expired: list[str] = []
    for nick, ent in list((doc.get("workers") or {}).items()):
        if not isinstance(ent, dict):
            expired.append(str(nick))
            continue
        ts = _parse_worker_ts(ent.get("ts"))
        if ts is None or (now_f - ts) >= float(ttl):
            expired.append(str(nick))
    for nick in expired:
        release_worker(home, nick, reason="stale")
        # FR #79/#91: clear both digest views
        try:
            from .digest import load_digest, mirror_top_worker_to_machine, save_digest

            dig = load_digest(home)
            mirror_top_worker_to_machine(dig, nick, None, remove=True)
            qw = dict((dig.get("queue") or {}).get("workers") or {})
            qw.pop(nick, None)
            dig.setdefault("queue", {})["workers"] = qw
            dig["workers"] = dict(qw)
            save_digest(home, dig)
        except Exception:
            pass
    return expired
