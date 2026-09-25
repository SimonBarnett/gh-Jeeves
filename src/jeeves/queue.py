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

# agentic_irc #207-compatible closes grammar (Closes/Fixes/Resolves/Refs #n).
_CLOSES_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)\b",
)


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


def top_unaccepted(home: Path) -> dict | None:
    doc = load_queue(home)
    rows = sorted(doc["unaccepted"], key=lambda r: int(r.get("seq") or 0))
    return rows[0] if rows else None


def format_offer(nick: str, row: dict) -> str:
    task = str(row.get("task") or "FR")
    repo = str(row.get("repo") or "")
    ident = str(row.get("id") or "")
    url = str(row.get("url") or f"https://github.com/{repo}/issues/{ident.lstrip('#')}")
    return f"{nick}: OFFER {task} {repo}{ident} {url}"


def _norm_ident(ident: str) -> str:
    s = str(ident or "").strip()
    if not s:
        return ""
    return s if s.startswith("#") else f"#{s}"


def _norm_repo(repo: str) -> str:
    return str(repo or "").strip()


def find_unaccepted(doc: dict, task: str, repo: str, ident: str) -> dict | None:
    """Locate unaccepted row (case-insensitive task; #n normalized)."""
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    for row in doc.get("unaccepted") or []:
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and str(row.get("task") or "").upper() == task_u
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            return row
    return None


def accept_job(
    home: Path,
    nick: str,
    channel: str,
    task: str,
    repo: str,
    ident: str,
) -> tuple[str, dict | None]:
    """K3 / FR #4: ACK path — unaccepted → accepted; worker busy.

    Idempotent: if already accepted by the same nick, return accepted again.
    """
    doc = load_queue(home)
    ident = _norm_ident(ident)
    repo = _norm_repo(repo)
    task_u = str(task or "").upper()
    nick_s = str(nick or "").strip()
    channel_s = str(channel or "").strip()

    # Already accepted by this nick?
    for row in list(doc.get("accepted") or []):
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and str(row.get("task") or "").upper() == task_u
            and _norm_ident(str(row.get("id") or "")) == ident
            and str(row.get("nick") or "") == nick_s
        ):
            doc["workers"][nick_s] = {
                "state": "busy",
                "job": f"{repo} {task_u} {ident}",
                "channel": channel_s or str(row.get("channel") or ""),
                "ts": _utc_now(),
            }
            save_queue(home, doc)
            return "accepted", row

    match = None
    for i, row in enumerate(list(doc["unaccepted"])):
        if (
            _norm_repo(str(row.get("repo") or "")) == repo
            and str(row.get("task") or "").upper() == task_u
            and _norm_ident(str(row.get("id") or "")) == ident
        ):
            match = doc["unaccepted"].pop(i)
            break
    if match is None:
        return "no_match", None

    match["nick"] = nick_s
    match["channel"] = channel_s
    match["accepted_ts"] = _utc_now()
    match["task"] = task_u
    match["id"] = ident
    match["repo"] = repo
    doc["accepted"].append(match)
    if len(doc["accepted"]) > ACCEPTED_CAP:
        doc["accepted"] = doc["accepted"][-ACCEPTED_CAP:]
    doc["workers"][nick_s] = {
        "state": "busy",
        "job": f"{repo} {task_u} {ident}",
        "channel": channel_s,
        "ts": _utc_now(),
    }
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
            and str(row.get("task") or "").upper() == task_u
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
        lambda r: _same(r, repo, task_u, ident),
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
    ident = ident if ident.startswith("#") else f"#{ident}"
    match = None
    for i, row in enumerate(list(doc["accepted"])):
        if (
            str(row.get("nick")) == nick
            and str(row.get("repo")) == repo
            and str(row.get("task")).upper() == task.upper()
            and str(row.get("id")) == ident
        ):
            match = doc["accepted"].pop(i)
            break
    if match is None:
        # still allow DONE to clear busy if row missing
        doc["workers"][nick] = {"state": "idle", "ts": _utc_now()}
        save_queue(home, doc)
        return "no_match", None
    match["done_ts"] = _utc_now()
    match["result"] = result
    if url:
        match["done_url"] = url
    doc["done"].append(match)
    if len(doc["done"]) > DONE_CAP:
        doc["done"] = doc["done"][-DONE_CAP:]
    doc["workers"][nick] = {"state": "idle", "ts": _utc_now()}
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
