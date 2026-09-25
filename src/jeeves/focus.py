"""FR #68: !focus priority-sort for !list and ear !bored (top_unaccepted).

Token-less. One sort feeds list + bored. Ignored repos stay hidden (#75).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

FOCUS_VERSION = 1
FOCUS_FILE = "focus.json"

# lower number = higher priority
NAMED_PRIORITY = {
    "high": 1,
    "medium": 5,
    "low": 9,
}
DEFAULT_PRIORITY = NAMED_PRIORITY["high"]
UNFOCUSED_RANK = 10_000  # after all focused

_REPO_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
_FOCUS_CMD = re.compile(r"^!+\s*focus(?:\s+(.*))?$", re.I)
_UNFOCUS_CMD = re.compile(r"^!+\s*unfocus(?:\s+(.*))?$", re.I)


def focus_path(home: Path) -> Path:
    return Path(home) / FOCUS_FILE


def empty_focus() -> dict[str, Any]:
    return {"v": FOCUS_VERSION, "repos": {}}


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_repo(token: str) -> str | None:
    """owner/name or bare name; accept github URLs."""
    s = (token or "").strip().strip("{}").strip()
    if not s:
        return None
    s = s.rstrip(".,;:!?)")
    for prefix in ("https://github.com/", "http://github.com/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.strip().strip("/")
    if not _REPO_TOKEN.match(s):
        return None
    return s


def load_focus(home: Path) -> dict[str, Any]:
    path = focus_path(home)
    if not path.is_file():
        return empty_focus()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_focus()
    if not isinstance(doc, dict):
        return empty_focus()
    doc.setdefault("v", FOCUS_VERSION)
    repos = doc.get("repos")
    if not isinstance(repos, dict):
        # legacy list form → high
        if isinstance(repos, list):
            converted: dict[str, Any] = {}
            for r in repos:
                key = normalize_repo(str(r or ""))
                if key:
                    converted[key] = {
                        "priority": DEFAULT_PRIORITY,
                        "label": "high",
                        "ts": _utc_now(),
                    }
            doc["repos"] = converted
        else:
            doc["repos"] = {}
    else:
        cleaned: dict[str, Any] = {}
        for k, v in repos.items():
            key = normalize_repo(str(k or ""))
            if not key:
                continue
            if isinstance(v, dict):
                pr = int(v.get("priority") or DEFAULT_PRIORITY)
                lab = str(v.get("label") or _label_for_priority(pr))
                cleaned[key] = {
                    "priority": max(1, pr),
                    "label": lab,
                    "ts": str(v.get("ts") or ""),
                }
            else:
                try:
                    pr = int(v)
                except (TypeError, ValueError):
                    pr = DEFAULT_PRIORITY
                cleaned[key] = {
                    "priority": max(1, pr),
                    "label": _label_for_priority(pr),
                    "ts": "",
                }
        doc["repos"] = cleaned
    return doc


def save_focus(home: Path, doc: dict[str, Any]) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = focus_path(home)
    payload = {
        "v": int(doc.get("v") or FOCUS_VERSION),
        "repos": dict(doc.get("repos") or {}),
        "updated": doc.get("updated") or _utc_now(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _label_for_priority(pr: int) -> str:
    for name, n in NAMED_PRIORITY.items():
        if n == pr:
            return name
    return str(pr)


def parse_priority_token(tok: str) -> tuple[int, str] | None:
    """Return (priority, label) or None if not a priority token."""
    t = (tok or "").strip().lower()
    if not t:
        return None
    if t in NAMED_PRIORITY:
        return NAMED_PRIORITY[t], t
    if t.isdigit():
        n = int(t)
        if n < 1:
            return None
        return n, _label_for_priority(n)
    return None


def focus_map(home: Path) -> dict[str, dict[str, Any]]:
    return dict(load_focus(home).get("repos") or {})


def repo_priority(home: Path, repo: str) -> int | None:
    """Numeric priority for repo, or None if unfocused."""
    m = focus_map(home)
    if not m:
        return None
    full = (repo or "").strip()
    if not full:
        return None
    full_l = full.lower()
    short_l = full.rsplit("/", 1)[-1].lower()
    # exact full match first
    for key, meta in m.items():
        if key.lower() == full_l:
            return int(meta.get("priority") or DEFAULT_PRIORITY)
    # bare focus key matches job short name; full focus key matches job short only if bare job
    for key, meta in m.items():
        kl = key.lower()
        kshort = key.rsplit("/", 1)[-1].lower()
        if "/" not in key and kl == short_l:
            return int(meta.get("priority") or DEFAULT_PRIORITY)
        if "/" in key and kshort == short_l and "/" not in full:
            return int(meta.get("priority") or DEFAULT_PRIORITY)
    return None


def sort_rank(home: Path, repo: str) -> int:
    pr = repo_priority(home, repo)
    return pr if pr is not None else UNFOCUSED_RANK


def focus_label_for_repo(home: Path, repo: str) -> str | None:
    """Label for !list tag, or None if unfocused."""
    m = focus_map(home)
    if not m:
        return None
    full = (repo or "").strip()
    full_l = full.lower()
    short_l = full.rsplit("/", 1)[-1].lower()
    for key, meta in m.items():
        kl = key.lower()
        kshort = key.rsplit("/", 1)[-1].lower()
        hit = kl == full_l or ("/" not in key and kl == short_l)
        if hit:
            lab = str(meta.get("label") or "").strip()
            if lab:
                return lab
            return _label_for_priority(int(meta.get("priority") or DEFAULT_PRIORITY))
    return None


def sort_unaccepted_rows(home: Path, rows: list[dict]) -> list[dict]:
    """
    Single sort for !list and !bored: focus priority ascending, then seq ascending.
    Ignored filtering is caller's responsibility (or apply before).
    """
    decorated = []
    for r in rows:
        repo = str(r.get("repo") or "")
        seq = int(r.get("seq") or 0)
        decorated.append((sort_rank(home, repo), seq, r))
    decorated.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in decorated]


def set_focus(
    home: Path,
    repo_token: str,
    *,
    priority: int = DEFAULT_PRIORITY,
    label: str | None = None,
) -> tuple[str, str | None, int]:
    """Returns (status, canonical, priority) status=set|bad."""
    canon = normalize_repo(repo_token)
    if not canon:
        return "bad", None, 0
    pr = max(1, int(priority))
    lab = label or _label_for_priority(pr)
    doc = load_focus(home)
    repos = dict(doc.get("repos") or {})
    short = canon.rsplit("/", 1)[-1].lower()
    key_l = canon.lower()
    # replace same key (case-insensitive) or bare/full pair on same short name
    cleaned: dict[str, Any] = {}
    for k, v in repos.items():
        kl = k.lower()
        kshort = k.rsplit("/", 1)[-1].lower()
        if kl == key_l:
            continue
        if kshort == short and (("/" in canon) == ("/" in k) or kl == short or kshort == key_l):
            # drop bare when setting full with same short, and vice versa
            if ("/" not in k and "/" in canon and kshort == short) or (
                "/" not in canon and "/" in k and kshort == short
            ):
                continue
        cleaned[k] = v
    repos = cleaned
    repos[canon] = {"priority": pr, "label": lab, "ts": _utc_now()}
    doc["repos"] = repos
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return "set", canon, pr


def remove_focus(home: Path, repo_token: str) -> tuple[str, str | None]:
    if (repo_token or "").strip().lower() == "all":
        doc = load_focus(home)
        n = len(doc.get("repos") or {})
        doc["repos"] = {}
        doc["updated"] = _utc_now()
        save_focus(home, doc)
        return "cleared", f"{n}"
    canon = normalize_repo(repo_token)
    if not canon:
        return "bad", None
    doc = load_focus(home)
    repos = dict(doc.get("repos") or {})
    short = canon.rsplit("/", 1)[-1].lower()
    key_l = canon.lower()
    kept: dict[str, Any] = {}
    removed = False
    for k, v in repos.items():
        kl = k.lower()
        if kl == key_l or (("/" not in canon) and (kl == short or k.rsplit("/", 1)[-1].lower() == short)):
            removed = True
            continue
        if "/" in canon and "/" not in k and kl == short:
            removed = True
            continue
        kept[k] = v
    if not removed:
        return "missing", canon
    doc["repos"] = kept
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return "removed", canon


def list_focus_entries(home: Path) -> list[tuple[str, int, str]]:
    """Sorted (repo, priority, label) for !focus bare list."""
    m = focus_map(home)
    rows = [(k, int(v.get("priority") or DEFAULT_PRIORITY), str(v.get("label") or "")) for k, v in m.items()]
    rows.sort(key=lambda t: (t[1], t[0].lower()))
    return rows


def format_focus_lines(home: Path) -> list[str]:
    rows = list_focus_entries(home)
    if not rows:
        return ["focus: (none)"]
    out = [f"focus ({len(rows)}):"]
    for repo, pr, lab in rows:
        tag = lab if lab in NAMED_PRIORITY else str(pr)
        out.append(f"  {pr} ({tag}) {repo}")
    return out


def handle_focus_cmd(home: Path, arg: str) -> list[str]:
    """arg is remainder after !focus (may be empty)."""
    raw = (arg or "").strip()
    if not raw:
        return format_focus_lines(home)
    parts = raw.split()
    if len(parts) == 1:
        # bare repo → high
        st, canon, pr = set_focus(home, parts[0], priority=DEFAULT_PRIORITY, label="high")
        if st == "bad":
            return ["focus: bad repo (use owner/name or URL)"]
        return [f"focus: {canon} priority={pr} (high)"]
    # first token priority?
    pri = parse_priority_token(parts[0])
    if pri is not None:
        pr, lab = pri
        repo_tok = " ".join(parts[1:]).strip()
        if not repo_tok:
            return ["focus: usage !focus [n|high|medium|low] {repo}"]
        st, canon, pr2 = set_focus(home, repo_tok, priority=pr, label=lab)
        if st == "bad":
            return ["focus: bad repo (use owner/name or URL)"]
        return [f"focus: {canon} priority={pr2} ({lab})"]
    # multi-token repo URL without priority
    st, canon, pr = set_focus(home, raw, priority=DEFAULT_PRIORITY, label="high")
    if st == "bad":
        return ["focus: usage !focus [n|high|medium|low] {repo}"]
    return [f"focus: {canon} priority={pr} (high)"]


def handle_unfocus_cmd(home: Path, arg: str) -> list[str]:
    raw = (arg or "").strip()
    if not raw:
        return ["unfocus: usage !unfocus {repo}|all"]
    st, canon = remove_focus(home, raw)
    if st == "bad":
        return ["unfocus: bad repo"]
    if st == "cleared":
        return [f"unfocus: cleared {canon} entries"]
    if st == "missing":
        return [f"unfocus: {canon} was not focused"]
    return [f"unfocus: removed {canon}"]


def parse_focus_cmd(body: str) -> str | None:
    """Return arg string (possibly '') if !focus, else None."""
    m = _FOCUS_CMD.match((body or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def parse_unfocus_cmd(body: str) -> str | None:
    m = _UNFOCUS_CMD.match((body or "").strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def is_focus_cmd(body: str) -> bool:
    return parse_focus_cmd(body) is not None


def is_unfocus_cmd(body: str) -> bool:
    return parse_unfocus_cmd(body) is not None


def focus_public_list(home: Path) -> list[dict[str, Any]]:
    """Digest-safe list: [{repo, priority, label}, ...] sorted."""
    return [
        {"repo": r, "priority": p, "label": lab}
        for r, p, lab in list_focus_entries(home)
    ]


def may_mutate_focus(nick: str, *, account: str | None = None, mode_grants_live: bool = False) -> bool:
    """Only simon (services account when mode_grants live)."""
    n = (nick or "").strip().lower()
    if n != "simon" and not n.startswith("simon-"):
        return False
    if mode_grants_live:
        return (account or "").strip().lower() == "simon"
    return True
