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

_CLOSES_RE = re.compile(
    r"(?:close[sd]?|fix[sd]?|resolve[sd]?)\s+#(\d+)",
    re.I,
)


@dataclass(frozen=True)
class Claim:
    repo: str
    task: str  # FR | MRB | UAT
    id: str  # #n
    event: str = ""
    action: str = ""
    line: str = ""
    url: str = ""

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
    """Map GitHub webhook event → queue claim (FR/MRB/UAT)."""
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
        url = str(issue.get("html_url") or "")
        if action in ("opened", "reopened"):
            return Claim(repo=full, task="FR", id=ident, event=event, action=action, line=title, url=url)
        if action == "closed":
            return Claim(repo=full, task="CLOSE", id=ident, event=event, action=action, line=title, url=url)
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
        if action in ("opened", "ready_for_review", "reopened"):
            return Claim(repo=full, task="MRB", id=ident, event=event, action=action, line=title, url=url)
        if action == "closed" and merged:
            # MRB PASS path → UAT of linked FR if closes #n present
            closes = extract_closes_issue_ids(title, body)
            fr_id = closes[0] if closes else ident
            return Claim(
                repo=full,
                task="UAT",
                id=fr_id if closes else ident,
                event=event,
                action="merged",
                line=title,
                url=url,
            )
        if action == "closed" and not merged:
            return Claim(repo=full, task="RESTORE_FR", id=ident, event=event, action=action, line=title, url=url)
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


def _append_unaccepted(doc: dict, claim: Claim, **extra: str) -> None:
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
    row.update({k: v for k, v in extra.items() if v is not None})
    # de-dupe same key
    _remove_matching(
        doc["unaccepted"],
        lambda r: _same(r, claim.repo, claim.task, claim.id),
    )
    doc["unaccepted"].append(row)


def apply_queue_event(home: Path, claim: Claim) -> str:
    """Apply supersede rules; return action tag for tests/logs."""
    doc = load_queue(home)
    repo, task, ident = claim.repo, claim.task, claim.id

    if task == "CLOSE":
        n = 0
        for bucket in ("unaccepted", "accepted"):
            n += _remove_matching(
                doc[bucket],
                lambda r, _id=ident, _repo=repo: str(r.get("repo")) == _repo and str(r.get("id")) == _id,
            )
        save_queue(home, doc)
        return f"removed:{n}"

    if task == "FR":
        # remove stale CLOSE nothing; add FR; drop prior FR same id
        _remove_matching(
            doc["unaccepted"],
            lambda r: str(r.get("repo")) == repo and str(r.get("id")) == ident and str(r.get("task")) in ("FR", "MRB", "UAT"),
        )
        _append_unaccepted(doc, claim)
        save_queue(home, doc)
        return "enqueued:FR"

    if task == "MRB":
        # FR+PR → MRB supersedes FR for linked issues in title/body already in claim.line
        closes = extract_closes_issue_ids(claim.line)
        for fr in closes:
            _remove_matching(
                doc["unaccepted"],
                lambda r, _fr=fr: str(r.get("repo")) == repo and str(r.get("id")) == _fr and str(r.get("task")) == "FR",
            )
        # also supersede any FR that matches repo if PR body stored elsewhere — caller may pass linked
        _remove_matching(
            doc["unaccepted"],
            lambda r: _same(r, repo, "MRB", ident),
        )
        _append_unaccepted(doc, claim)
        save_queue(home, doc)
        return "enqueued:MRB"

    if task == "UAT":
        # remove MRB for this PR / FR
        _remove_matching(
            doc["unaccepted"],
            lambda r: str(r.get("repo")) == repo
            and str(r.get("task")) in ("MRB", "FR", "UAT")
            and str(r.get("id")) in (ident, claim.id),
        )
        _remove_matching(
            doc["accepted"],
            lambda r: str(r.get("repo")) == repo and str(r.get("id")) == ident,
        )
        _append_unaccepted(doc, claim)
        save_queue(home, doc)
        return "enqueued:UAT"

    if task == "RESTORE_FR":
        # PR closed unmerged: drop MRB, restore FR if we know linked id from line
        _remove_matching(
            doc["unaccepted"],
            lambda r: _same(r, repo, "MRB", ident),
        )
        _remove_matching(
            doc["accepted"],
            lambda r: _same(r, repo, "MRB", ident),
        )
        closes = extract_closes_issue_ids(claim.line)
        for fr in closes or ():
            _append_unaccepted(
                doc,
                Claim(repo=repo, task="FR", id=fr, event=claim.event, action="restore", line=claim.line, url=claim.url),
            )
        save_queue(home, doc)
        return "restored:FR"

    _append_unaccepted(doc, claim)
    save_queue(home, doc)
    return f"enqueued:{task}"


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
