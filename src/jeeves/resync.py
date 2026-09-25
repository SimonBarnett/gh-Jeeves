"""Rebuild task list from GitHub (start + periodic). Scripts only; no LLM.

FR #25: outstanding FR/MRB/UAT from API fixture or live REST, reconcile with
queue.json, keep live accepted workers, quiet when unchanged.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .length_safe import VitalFields, format_announce, prepare_send
from .queue import (
    Claim,
    apply_queue_event,
    empty_queue,
    extract_closes_issue_ids,
    load_queue,
    save_queue,
)

_CLOSES = re.compile(
    r"(?i)(?:close[sd]?|fix[sd]?|resolve[sd]?)\s+#(\d+)",
)

UAT_LABELS = frozenset({"uat", "ready-for-uat", "awaiting-uat"})
UAT_STAMP_MARKERS = (
    "uat stamped",
    "bob stamps uat",
    "uat: pass",
    "ready for human uat",
)


class GitHubClient(Protocol):
    def list_repos(self) -> list[str]:
        """owner/name full names that carry the Bob GIT webhook (or allow list)."""

    def list_open_issues(self, repo: str) -> list[dict[str, Any]]:
        ...

    def list_open_pulls(self, repo: str) -> list[dict[str, Any]]:
        ...

    def list_recent_closed_pulls(self, repo: str) -> list[dict[str, Any]]:
        """Merged/closed PRs needed for UAT + restore logic."""


@dataclass
class ResyncConfig:
    owners: list[str] = field(default_factory=lambda: ["SimonBarnett"])
    allow_repos: list[str] = field(default_factory=list)  # if set, only these
    deny_repos: list[str] = field(default_factory=list)
    interval_s: float = 15 * 60
    webhook_url_suffix: str = "/bob/v1/git"
    github_api: str = "https://api.github.com"
    token: str | None = None  # never logged
    announce_channel: str = "#bobiverse"


@dataclass
class DiffStats:
    added: int = 0
    removed: int = 0
    retyped: int = 0
    kept: int = 0
    accepted_kept: int = 0
    accepted_released: int = 0
    skipped_github_down: bool = False
    quiet: bool = False  # no material change

    @property
    def total_delta(self) -> int:
        return self.added + self.removed + self.retyped

    def summary_line(self, total: int) -> str:
        """Length-safe one-liner for #bobiverse (FR #24 budget)."""
        if self.skipped_github_down:
            body = f"Jeeves resync: github-down kept-queue total {total}"
        elif self.quiet:
            body = f"Jeeves resync: unchanged total {total}"
        else:
            body = (
                f"Jeeves resync: +{self.added} -{self.removed} ~{self.retyped}, "
                f"total {total}"
            )
        vital = VitalFields(
            event="resync",
            task="OTHER",
            ref="queue",
            action="resync",
            title=body,
        )
        # Prefer human phrase as full line when under budget
        res = format_announce(vital)
        # Use plain body if it fits wire; else compact GIT form
        from .length_safe import wire_line_bytes

        if wire_line_bytes(body) <= 512:
            return body
        return res.line


@dataclass
class FakeGitHub:
    """In-memory GitHub for G1 / FR #25 acceptance tests."""

    repos: list[str] = field(default_factory=list)
    issues: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pulls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    closed_pulls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    down: bool = False

    def list_repos(self) -> list[str]:
        if self.down:
            raise URLError("github down")
        return list(self.repos)

    def list_open_issues(self, repo: str) -> list[dict[str, Any]]:
        if self.down:
            raise URLError("github down")
        return list(self.issues.get(repo) or [])

    def list_open_pulls(self, repo: str) -> list[dict[str, Any]]:
        if self.down:
            raise URLError("github down")
        return list(self.pulls.get(repo) or [])

    def list_recent_closed_pulls(self, repo: str) -> list[dict[str, Any]]:
        if self.down:
            raise URLError("github down")
        return list(self.closed_pulls.get(repo) or [])


def _num_id(n: Any) -> str:
    return f"#{int(n)}"


def _created_ts(item: dict[str, Any]) -> float:
    raw = item.get("created_at") or item.get("created") or ""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str) and raw:
        # 2024-01-02T03:04:05Z
        try:
            from datetime import datetime

            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return float(item.get("number") or 0)


def _pr_closes(pr: dict[str, Any]) -> tuple[str, ...]:
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    return extract_closes_issue_ids(title, body)


def _has_uat_stamp(pr: dict[str, Any]) -> bool:
    labels = pr.get("labels") or []
    names = set()
    for lab in labels:
        if isinstance(lab, dict):
            names.add(str(lab.get("name") or "").lower())
        else:
            names.add(str(lab).lower())
    if names & UAT_LABELS:
        # label means awaiting UAT, not stamped
        pass
    comments = pr.get("comments_text") or pr.get("uat_comments") or ""
    blob = f"{pr.get('body') or ''}\n{comments}".lower()
    if any(m in blob for m in UAT_STAMP_MARKERS):
        return True
    if pr.get("uat_stamped") is True:
        return True
    return False


def _awaiting_uat(pr: dict[str, Any]) -> bool:
    if not pr.get("merged"):
        return False
    if _has_uat_stamp(pr):
        return False
    # merged after MRB PASS: treat as UAT unless stamp present
    return True


def build_outstanding(
    client: GitHubClient,
    cfg: ResyncConfig | None = None,
    *,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Deterministic outstanding tasks (same supersede rules as live engine)."""
    cfg = cfg or ResyncConfig()
    repos = client.list_repos()
    if cfg.allow_repos:
        allow = {r.lower() for r in cfg.allow_repos}
        repos = [r for r in repos if r.lower() in allow]
    if cfg.deny_repos:
        deny = {r.lower() for r in cfg.deny_repos}
        repos = [r for r in repos if r.lower() not in deny]
    # FR #75: runtime ignore list (ignored.json) — skip entire repo in resync.
    if home is not None:
        from .ignore import ignored_list, repo_is_ignored

        ign = ignored_list(Path(home))
        if ign:
            repos = [r for r in repos if not repo_is_ignored(r, ign)]

    rows: list[dict[str, Any]] = []
    for repo in repos:
        issues = client.list_open_issues(repo)
        pulls = client.list_open_pulls(repo)
        closed = client.list_recent_closed_pulls(repo)

        # Map issue number -> superseded by open PR
        superseded_fr: set[str] = set()
        for pr in pulls:
            for fr in _pr_closes(pr):
                superseded_fr.add(fr)
            # open PR is always MRB
            num = pr.get("number")
            if num is None:
                continue
            rows.append(
                {
                    "repo": repo,
                    "task": "MRB",
                    "id": _num_id(num),
                    "line": str(pr.get("title") or "")[:120],
                    "url": str(pr.get("html_url") or ""),
                    "created_at": pr.get("created_at") or "",
                    "seq": int(_created_ts(pr) * 1000) + int(num),
                    "event": "resync",
                    "action": "open_pr",
                }
            )

        for issue in issues:
            num = issue.get("number")
            if num is None:
                continue
            # skip PRs mis-listed as issues
            if issue.get("pull_request"):
                continue
            ident = _num_id(num)
            if ident in superseded_fr:
                continue
            rows.append(
                {
                    "repo": repo,
                    "task": "FR",
                    "id": ident,
                    "line": str(issue.get("title") or "")[:120],
                    "url": str(issue.get("html_url") or ""),
                    "created_at": issue.get("created_at") or "",
                    "seq": int(_created_ts(issue) * 1000) + int(num),
                    "event": "resync",
                    "action": "open_issue",
                }
            )

        for pr in closed:
            num = pr.get("number")
            if num is None:
                continue
            if pr.get("merged") and _awaiting_uat(pr):
                closes = _pr_closes(pr)
                # UAT keyed to FR if closes #n else PR number
                uat_id = closes[0] if closes else _num_id(num)
                rows.append(
                    {
                        "repo": repo,
                        "task": "UAT",
                        "id": uat_id,
                        "line": str(pr.get("title") or "")[:120],
                        "url": str(pr.get("html_url") or ""),
                        "created_at": pr.get("merged_at") or pr.get("closed_at") or pr.get("created_at") or "",
                        "seq": int(_created_ts(pr) * 1000) + int(num) + 10_000,
                        "event": "resync",
                        "action": "merged_awaiting_uat",
                        "pr": _num_id(num),
                    }
                )
            # closed unmerged: FR restored only if issue still open — already covered
            # by open issues list if issue open; no MRB row for closed unmerged

    rows.sort(key=lambda r: (int(r.get("seq") or 0), str(r.get("id") or "")))
    return rows


def _row_key(row: dict[str, Any]) -> str:
    return f"{row.get('repo')}|{row.get('task')}|{row.get('id')}"


def _identity_key(row: dict[str, Any]) -> str:
    """Repo+#id ignoring task (for retype detection)."""
    return f"{row.get('repo')}|{row.get('id')}"


def reconcile_queue(
    home: Path,
    desired: list[dict[str, Any]],
    *,
    connected_nicks: set[str] | None = None,
) -> DiffStats:
    """
    Merge desired outstanding into queue.json.
    Keep accepted rows whose nick is still connected; else release to unaccepted
    if still outstanding, or drop if GitHub says finished.
    """
    connected_nicks = connected_nicks or set()
    doc = load_queue(home)
    stats = DiffStats()

    # FR #75: strip ignored repos from live queue during reconcile.
    from .ignore import filter_rows_not_ignored, ignored_list, repo_is_ignored

    ign = ignored_list(home)
    if ign:
        for bucket in ("unaccepted", "accepted"):
            before_n = len(doc.get(bucket) or [])
            doc[bucket] = filter_rows_not_ignored(home, list(doc.get(bucket) or []))
            removed_n = before_n - len(doc[bucket])
            if removed_n:
                stats.removed += removed_n
        desired = [r for r in desired if not repo_is_ignored(str(r.get("repo") or ""), ign)]

    desired_by_key = {_row_key(r): r for r in desired}
    desired_by_ident = {}
    for r in desired:
        desired_by_ident[_identity_key(r)] = r

    # --- accepted reconcile ---
    new_accepted: list[dict] = []
    released: list[dict] = []
    for row in list(doc.get("accepted") or []):
        nick = str(row.get("nick") or "")
        key = _row_key(row)
        ident = _identity_key(row)
        still_out = key in desired_by_key or ident in desired_by_ident
        if nick and nick in connected_nicks and still_out:
            # refresh task type from GitHub if retyped
            if ident in desired_by_ident and str(row.get("task")) != str(desired_by_ident[ident].get("task")):
                row = {**row, **{k: desired_by_ident[ident][k] for k in ("task", "line", "url", "seq") if k in desired_by_ident[ident]}}
                stats.retyped += 1
            new_accepted.append(row)
            stats.accepted_kept += 1
            stats.kept += 1
            # remove from desired so not double in unaccepted
            desired_by_key.pop(_row_key(row), None)
            if _identity_key(row) in desired_by_ident:
                # also pop matching desired key
                d = desired_by_ident[_identity_key(row)]
                desired_by_key.pop(_row_key(d), None)
            continue
        if nick and nick not in connected_nicks:
            stats.accepted_released += 1
            if still_out:
                # back to unaccepted with GitHub shape
                src = desired_by_ident.get(ident) or desired_by_key.get(key)
                if src:
                    released.append(dict(src))
                    desired_by_key.pop(_row_key(src), None)
                else:
                    released.append({k: v for k, v in row.items() if k not in ("nick", "channel", "accepted_ts")})
            # else drop (finished on GitHub)
            continue
        # no nick or disconnected path already handled; drop if not outstanding
        if still_out:
            src = desired_by_ident.get(ident) or row
            released.append(dict(src) if isinstance(src, dict) else row)
            desired_by_key.pop(_row_key(src) if isinstance(src, dict) else key, None)
        else:
            stats.removed += 1

    # --- unaccepted: replace from desired remaining + released ---
    old_unacc = list(doc.get("unaccepted") or [])
    old_keys = {_row_key(r): r for r in old_unacc}
    old_idents = {_identity_key(r): r for r in old_unacc}

    new_unacc: list[dict] = []
    seen: set[str] = set()

    for row in released:
        k = _row_key(row)
        if k in seen:
            continue
        seen.add(k)
        new_unacc.append(row)

    for key, row in desired_by_key.items():
        if key in seen:
            continue
        # skip if still in new_accepted
        if any(_row_key(a) == key or _identity_key(a) == _identity_key(row) for a in new_accepted):
            continue
        seen.add(key)
        new_unacc.append(row)

    # sort oldest first
    new_unacc.sort(key=lambda r: (int(r.get("seq") or 0), str(r.get("id") or "")))

    new_keys = {_row_key(r) for r in new_unacc}
    new_idents = {_identity_key(r): r for r in new_unacc}

    for k, old in old_keys.items():
        if k in new_keys:
            stats.kept += 1
        elif _identity_key(old) in new_idents and str(old.get("task")) != str(new_idents[_identity_key(old)].get("task")):
            stats.retyped += 1
        else:
            # removed from unaccepted (unless moved to accepted)
            if not any(_identity_key(a) == _identity_key(old) for a in new_accepted):
                stats.removed += 1

    for k, row in {_row_key(r): r for r in new_unacc}.items():
        if k not in old_keys:
            if _identity_key(row) in old_idents and str(old_idents[_identity_key(row)].get("task")) != str(row.get("task")):
                pass  # counted retyped
            elif not any(_identity_key(a) == _identity_key(row) for a in list(doc.get("accepted") or [])):
                stats.added += 1

    # drop stale done entries that are still "outstanding" — leave done as historical
    doc["unaccepted"] = new_unacc
    doc["accepted"] = new_accepted
    doc["resync"] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "added": stats.added,
        "removed": stats.removed,
        "retyped": stats.retyped,
        "total": len(new_unacc) + len(new_accepted),
    }
    save_queue(home, doc)

    if stats.added == 0 and stats.removed == 0 and stats.retyped == 0 and stats.accepted_released == 0:
        stats.quiet = True
    return stats


def run_resync(
    home: Path,
    client: GitHubClient,
    *,
    cfg: ResyncConfig | None = None,
    connected_nicks: set[str] | None = None,
    outbox_append: Callable[[str], None] | None = None,
    quiet_when_unchanged: bool = True,
) -> DiffStats:
    """
    Full resync. On GitHub failure: keep queue.json, mark skipped, do not wipe.
    """
    cfg = cfg or ResyncConfig()
    home = Path(home)
    try:
        desired = build_outstanding(client, cfg, home=home)
    except (URLError, HTTPError, OSError, TimeoutError) as exc:
        doc = load_queue(home)
        total = len(doc.get("unaccepted") or []) + len(doc.get("accepted") or [])
        stats = DiffStats(skipped_github_down=True, quiet=True)
        # never empty the queue because GitHub was down
        if outbox_append and not quiet_when_unchanged:
            outbox_append(stats.summary_line(total))
        log_path = home / "resync.log"
        prev = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        log_path.write_text(prev + f"\n{time.time()} github-down {exc!r}\n", encoding="utf-8")
        return stats

    before = load_queue(home)
    before_snap = json.dumps(
        {
            "u": sorted(_row_key(r) for r in (before.get("unaccepted") or [])),
            "a": sorted(_row_key(r) for r in (before.get("accepted") or [])),
        },
        sort_keys=True,
    )

    stats = reconcile_queue(home, desired, connected_nicks=connected_nicks)
    after = load_queue(home)
    after_snap = json.dumps(
        {
            "u": sorted(_row_key(r) for r in (after.get("unaccepted") or [])),
            "a": sorted(_row_key(r) for r in (after.get("accepted") or [])),
        },
        sort_keys=True,
    )
    if before_snap == after_snap:
        stats.quiet = True
        stats.added = 0
        stats.removed = 0
        stats.retyped = 0

    total = len(after.get("unaccepted") or []) + len(after.get("accepted") or [])
    line = stats.summary_line(total)
    # always log diff
    log_path = home / "resync.log"
    prev = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    log_path.write_text(
        prev + f"\n{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line} quiet={stats.quiet}\n",
        encoding="utf-8",
    )

    if outbox_append and not (quiet_when_unchanged and stats.quiet):
        outbox_append(line)

    return stats


def apply_webhook_during_resync(home: Path, claim: Claim) -> str:
    """Idempotent apply of live webhook on top of resync (no double queue)."""
    return apply_queue_event(home, claim)


class ResyncScheduler:
    """Start + periodic resync; on-demand trigger."""

    def __init__(
        self,
        home: Path,
        client: GitHubClient,
        *,
        cfg: ResyncConfig | None = None,
        connected_nicks_fn: Callable[[], set[str]] | None = None,
        outbox_append: Callable[[str], None] | None = None,
    ):
        self.home = Path(home)
        self.client = client
        self.cfg = cfg or ResyncConfig()
        self.connected_nicks_fn = connected_nicks_fn or (lambda: set())
        self.outbox_append = outbox_append
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: DiffStats | None = None
        self.runs = 0

    def run_once(self) -> DiffStats:
        self.last = run_resync(
            self.home,
            self.client,
            cfg=self.cfg,
            connected_nicks=self.connected_nicks_fn(),
            outbox_append=self.outbox_append,
        )
        self.runs += 1
        return self.last

    def start(self, *, run_immediately: bool = True) -> None:
        if run_immediately:
            self.run_once()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jeeves-resync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        interval = max(5.0, float(self.cfg.interval_s))
        while not self._stop.wait(interval):
            try:
                self.run_once()
            except Exception:
                continue


class ResyncConfigError(RuntimeError):
    """Missing credential or invalid resync configuration (FR #49)."""


def resync_disabled() -> bool:
    """True when operator opts out (tests / offline)."""
    v = (os.environ.get("JEEVES_RESYNC_DISABLE") or "").strip().lower()
    return v in ("1", "true", "yes", "off")


def load_github_token(*, env: dict | None = None, homes: list[Path] | None = None) -> str | None:
    """
    Read GitHub token for resync. Never log the value.

    Order: GITHUB_TOKEN, JEEVES_GITHUB_TOKEN, then first existing file among
    JEEVES_GITHUB_TOKEN_FILE, ~/.grok/github.token, {jeeves_home}/github.token,
    {digest_home}/github.token.
    """
    e = env if env is not None else os.environ
    for key in ("GITHUB_TOKEN", "JEEVES_GITHUB_TOKEN"):
        raw = (e.get(key) or "").strip()
        if raw:
            return raw
    paths: list[Path] = []
    file_env = (e.get("JEEVES_GITHUB_TOKEN_FILE") or "").strip()
    if file_env:
        paths.append(Path(file_env).expanduser())
    paths.append(Path.home() / ".grok" / "github.token")
    for h in homes or []:
        if h:
            paths.append(Path(h).expanduser() / "github.token")
    for p in paths:
        try:
            if p.is_file():
                tok = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                if tok:
                    return tok
        except OSError:
            continue
    return None


def build_resync_scheduler(
    home: Path,
    *,
    interval_s: float = 15 * 60,
    client: GitHubClient | None = None,
    require_token: bool = True,
    owners: list[str] | None = None,
    allow_repos: list[str] | None = None,
    github_api: str | None = None,
    jeeves_home: Path | None = None,
    outbox_append: Callable[[str], None] | None = None,
    connected_nicks_fn: Callable[[], set[str]] | None = None,
) -> ResyncScheduler | None:
    """
    Wire resync for the Windows service (FR #49).

    Returns None only when ``JEEVES_RESYNC_DISABLE`` is set.
    Raises ``ResyncConfigError`` if resync is enabled and no token/client.
    Loads existing queue.json implicitly via run_resync (never wipes on GitHub down).
    """
    if resync_disabled():
        return None
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    # Ensure queue.json exists so start never invents empty from missing file alone
    if not (home / "queue.json").is_file():
        from .queue import empty_queue, save_queue

        save_queue(home, empty_queue())

    cfg = ResyncConfig(
        owners=list(owners or ["SimonBarnett"]),
        allow_repos=list(allow_repos or []),
        interval_s=float(interval_s),
        github_api=github_api or "https://api.github.com",
        token=None,
    )
    gh: GitHubClient
    if client is not None:
        gh = client
    else:
        token = load_github_token(homes=[jeeves_home, home] if jeeves_home else [home])
        if not token:
            if require_token:
                raise ResyncConfigError(
                    "resync enabled but no GitHub token: set GITHUB_TOKEN or "
                    "JEEVES_GITHUB_TOKEN (or token file); or JEEVES_RESYNC_DISABLE=1"
                )
            return None
        cfg.token = token  # never print
        gh = LiveGitHubClient(cfg)

    def _append(line: str) -> None:
        if outbox_append:
            outbox_append(line)
            return
        # default: chair-outbox for #bobiverse quiet summary when not quiet
        path = home / "chair-outbox.txt"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"PRIVMSG #bobiverse :{line}\n")

    return ResyncScheduler(
        home,
        gh,
        cfg=cfg,
        connected_nicks_fn=connected_nicks_fn,
        outbox_append=_append,
    )


# --- optional thin live client (tests mock; production uses token from secure store) ---


class LiveGitHubClient:
    """Minimal REST client. Token never printed. Loopback-safe for tests via api base."""

    def __init__(self, cfg: ResyncConfig, *, opener=None):
        self.cfg = cfg
        self._opener = opener

    def _get(self, path: str) -> Any:
        url = self.cfg.github_api.rstrip("/") + path
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "gh-Jeeves-resync"}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_repos(self) -> list[str]:
        if self.cfg.allow_repos:
            return list(self.cfg.allow_repos)
        out: list[str] = []
        for owner in self.cfg.owners:
            page = self._get(f"/users/{owner}/repos?per_page=100&type=owner")
            if not isinstance(page, list):
                continue
            for r in page:
                name = r.get("full_name")
                if name:
                    out.append(str(name))
        return out

    def list_open_issues(self, repo: str) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        data = self._get(f"/repos/{owner}/{name}/issues?state=open&per_page=100")
        return [i for i in data if isinstance(i, dict) and "pull_request" not in i]

    def list_open_pulls(self, repo: str) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        data = self._get(f"/repos/{owner}/{name}/pulls?state=open&per_page=100")
        return list(data) if isinstance(data, list) else []

    def list_recent_closed_pulls(self, repo: str) -> list[dict[str, Any]]:
        owner, name = repo.split("/", 1)
        data = self._get(f"/repos/{owner}/{name}/pulls?state=closed&per_page=50&sort=updated")
        return list(data) if isinstance(data, list) else []
