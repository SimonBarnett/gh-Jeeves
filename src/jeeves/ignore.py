"""Repo ignore list (FR #75): suppress a repo from the whole Jeeves process.

Persists as ignored.json alongside queue.json. Token-less; no GitHub call.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

IGNORE_VERSION = 1
IGNORE_FILE = "ignored.json"

# bare name or owner/name
_REPO_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")

_IGNORE_CMD = re.compile(r"^!+\s*ignore(?:\s+(\S+))?\s*$", re.I)
_UNIGNORE_CMD = re.compile(r"^!+\s*unignore(?:\s+(\S+))?\s*$", re.I)
_IGNORED_CMD = re.compile(r"^!+\s*ignored\s*$", re.I)


def ignore_path(home: Path) -> Path:
    return Path(home) / IGNORE_FILE


def empty_ignored() -> dict[str, Any]:
    return {"v": IGNORE_VERSION, "repos": []}


def load_ignored(home: Path) -> dict[str, Any]:
    path = ignore_path(home)
    if not path.is_file():
        return empty_ignored()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_ignored()
    if not isinstance(doc, dict):
        return empty_ignored()
    doc.setdefault("v", IGNORE_VERSION)
    repos = doc.get("repos")
    if not isinstance(repos, list):
        doc["repos"] = []
    else:
        # normalize to unique strings, preserve first-seen casing order
        seen: set[str] = set()
        out: list[str] = []
        for r in repos:
            s = str(r or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        doc["repos"] = out
    return doc


def save_ignored(home: Path, doc: dict[str, Any]) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = ignore_path(home)
    tmp = path.with_suffix(".tmp")
    payload = {
        "v": int(doc.get("v") or IGNORE_VERSION),
        "repos": list(doc.get("repos") or []),
        "updated": doc.get("updated") or _utc_now(),
    }
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_repo_token(token: str) -> str | None:
    """Return cleaned repo token (name or owner/name) or None if invalid."""
    s = (token or "").strip().strip("{}").strip()
    if not s:
        return None
    # drop trailing punctuation often typed in chat
    s = s.rstrip(".,;:!?)")
    if s.startswith("https://github.com/"):
        s = s[len("https://github.com/") :]
    if s.startswith("http://github.com/"):
        s = s[len("http://github.com/") :]
    s = s.strip().strip("/")
    if not _REPO_TOKEN.match(s):
        return None
    return s


def repo_short_name(repo: str) -> str:
    r = (repo or "").strip()
    if "/" in r:
        return r.rsplit("/", 1)[-1]
    return r


def repo_is_ignored(repo: str, ignored_repos: list[str] | tuple[str, ...]) -> bool:
    """Case-insensitive match: full owner/name or bare name equals short name."""
    full = (repo or "").strip().lower()
    if not full:
        return False
    short = repo_short_name(full).lower()
    for raw in ignored_repos:
        ig = (raw or "").strip().lower()
        if not ig:
            continue
        if "/" in ig:
            if full == ig:
                return True
        else:
            if short == ig or full == ig:
                return True
    return False


def is_ignored(home: Path, repo: str) -> bool:
    doc = load_ignored(home)
    return repo_is_ignored(repo, list(doc.get("repos") or []))


def ignored_list(home: Path) -> list[str]:
    return list(load_ignored(home).get("repos") or [])


def add_ignore(home: Path, token: str) -> tuple[str, str | None]:
    """
    Add repo to ignore list.
    Returns (status, canonical) where status is added|exists|bad.
    """
    canon = normalize_repo_token(token)
    if not canon:
        return "bad", None
    doc = load_ignored(home)
    repos = list(doc.get("repos") or [])
    if any((r or "").strip().lower() == canon.lower() for r in repos):
        return "exists", canon
    # drop weaker bare-name / full-name duplicates that match the same short name
    # when adding full name, remove bare short; when adding bare, leave fulls
    if "/" in canon:
        short = repo_short_name(canon).lower()
        repos = [r for r in repos if (r or "").strip().lower() != short]
    repos.append(canon)
    doc["repos"] = repos
    doc["updated"] = _utc_now()
    save_ignored(home, doc)
    return "added", canon


def remove_ignore(home: Path, token: str) -> tuple[str, str | None]:
    """Remove matching ignore entry. Returns (removed|missing|bad, canonical)."""
    canon = normalize_repo_token(token)
    if not canon:
        return "bad", None
    doc = load_ignored(home)
    repos = list(doc.get("repos") or [])
    key = canon.lower()
    short = repo_short_name(canon).lower()
    kept: list[str] = []
    removed = False
    for r in repos:
        rl = (r or "").strip().lower()
        if rl == key or (("/" not in key) and (rl == short or repo_short_name(rl) == short)):
            removed = True
            continue
        # full ignore entry matches bare unignore of short name
        if "/" not in key and "/" in rl and repo_short_name(rl) == short:
            removed = True
            continue
        # bare ignore entry matches full unignore
        if "/" in key and "/" not in rl and rl == short:
            removed = True
            continue
        kept.append(r)
    if not removed:
        return "missing", canon
    doc["repos"] = kept
    doc["updated"] = _utc_now()
    save_ignored(home, doc)
    return "removed", canon


def purge_repo_from_queue(home: Path, repo_token: str) -> int:
    """Drop unaccepted/accepted/done rows matching ignored repo. Returns count removed."""
    from .queue import load_queue, save_queue

    canon = normalize_repo_token(repo_token) or (repo_token or "").strip()
    if not canon:
        return 0
    doc = load_queue(home)
    n = 0
    for bucket in ("unaccepted", "accepted", "done"):
        rows = list(doc.get(bucket) or [])
        kept: list[dict] = []
        for row in rows:
            r = str(row.get("repo") or "")
            if repo_is_ignored(r, [canon]):
                n += 1
                continue
            kept.append(row)
        doc[bucket] = kept
    if n:
        save_queue(home, doc)
    return n


def filter_rows_not_ignored(home: Path, rows: list[dict]) -> list[dict]:
    """Drop queue rows whose repo is ignored (also used by !focus layering)."""
    ignored = ignored_list(home)
    if not ignored:
        return list(rows)
    return [r for r in rows if not repo_is_ignored(str(r.get("repo") or ""), ignored)]


def repo_from_payload(payload: dict) -> str:
    repo_obj = (payload or {}).get("repository") or {}
    full = str(repo_obj.get("full_name") or "").strip()
    if full:
        return full
    name = str(repo_obj.get("name") or "").strip()
    if not name:
        return ""
    owner = (repo_obj.get("owner") or {})
    login = str(owner.get("login") or "").strip() if isinstance(owner, dict) else ""
    return f"{login}/{name}" if login else name


def may_mutate_ignore(nick: str, *, account: str | None = None) -> bool:
    """Owner (simon / simon-* + services account) or ops (bob-*)."""
    from .focus import owner_account_name

    n = (nick or "").strip().lower()
    if n.startswith("bob-"):
        return True
    owner = owner_account_name()
    if n == owner or n.startswith(f"{owner}-"):
        if account is None:
            # tests / no mode_grants: nick alone is enough for owner
            return True
        return (account or "").strip().lower() == owner
    return False


def parse_ignore_cmd(body: str) -> str | None:
    """Return repo arg for !ignore, '' if bare (bad), None if not a match."""
    m = _IGNORE_CMD.match((body or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_unignore_cmd(body: str) -> str | None:
    m = _UNIGNORE_CMD.match((body or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def is_ignored_cmd(body: str) -> bool:
    return bool(_IGNORED_CMD.match((body or "").strip()))


def is_ignore_cmd(body: str) -> bool:
    return parse_ignore_cmd(body) is not None


def is_unignore_cmd(body: str) -> bool:
    return parse_unignore_cmd(body) is not None


def format_ignored_lines(home: Path) -> list[str]:
    repos = ignored_list(home)
    if not repos:
        return ["ignored: (none)"]
    lines = [f"ignored ({len(repos)}):"]
    for r in repos:
        lines.append(f"  {r}")
    return lines


def handle_ignore_add(home: Path, token: str) -> list[str]:
    st, canon = add_ignore(home, token)
    if st == "bad":
        return ["ignore: bad repo (use name or owner/name)"]
    purged = purge_repo_from_queue(home, canon or token)
    if st == "exists":
        return [f"ignore: already ignoring {canon} (purged {purged} queued)"]
    return [f"ignore: now ignoring {canon} (purged {purged} queued)"]


def handle_unignore(home: Path, token: str) -> list[str]:
    st, canon = remove_ignore(home, token)
    if st == "bad":
        return ["unignore: bad repo (use name or owner/name)"]
    if st == "missing":
        return [f"unignore: {canon} was not ignored"]
    return [f"unignore: resumed {canon} (new events only)"]
