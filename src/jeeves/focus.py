"""FR #68 / #113 / #141: !focus priority-sort for !list and Jeeves assign-on-!bored.

Token-less. One sort feeds list + bored. Ignored repos stay hidden (#75).
FR #113: item keys ``owner/repo#N`` (or short ``repo#N``) rank ahead of repo focus.
FR #141: ``!focus strict on|off`` — when on, only item-focused or repo-focused
jobs are listed/assigned; empty focus → nothing queued.
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
# owner/repo#123 or repo#123 (optional spaces around #)
_ITEM_TOKEN = re.compile(
    r"^([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)\s*#\s*(\d+)\s*$",
    re.I,
)
_FOCUS_CMD = re.compile(r"^!+\s*focus(?:\s+(.*))?$", re.I)
_UNFOCUS_CMD = re.compile(r"^!+\s*unfocus(?:\s+(.*))?$", re.I)


def focus_path(home: Path) -> Path:
    return Path(home) / FOCUS_FILE


def empty_focus() -> dict[str, Any]:
    return {
        "v": FOCUS_VERSION,
        "repos": {},
        "items": {},
        "item_seq": 0,
        "strict": False,
    }


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
    # strip trailing #N if accidentally included
    if "#" in s and _ITEM_TOKEN.match(s):
        return None
    if not _REPO_TOKEN.match(s):
        return None
    return s


def normalize_item_ref(token: str) -> tuple[str, str, str] | None:
    """Return (repo_token, #id, canonical_key) for owner/repo#N or repo#N / URL#N."""
    s = (token or "").strip().strip("{}").strip()
    if not s:
        return None
    s = s.rstrip(".,;:!?)")
    for prefix in ("https://github.com/", "http://github.com/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix) :]
            # issues/N or pull/N → #N
            m_url = re.match(
                r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/(\d+)\s*$",
                s,
                re.I,
            )
            if m_url:
                repo = m_url.group(1)
                ident = f"#{int(m_url.group(2))}"
                return repo, ident, f"{repo}#{ident.lstrip('#')}"
            break
    m = _ITEM_TOKEN.match(s)
    if not m:
        return None
    repo = m.group(1)
    ident = f"#{int(m.group(2))}"
    return repo, ident, f"{repo}#{ident.lstrip('#')}"


def _clean_items(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in raw.items():
        parsed = normalize_item_ref(str(k or ""))
        if not parsed:
            continue
        repo, ident, key = parsed
        if isinstance(v, dict):
            rank = int(v.get("rank") or v.get("priority") or DEFAULT_PRIORITY)
            cleaned[key] = {
                "rank": max(1, rank),
                "repo": str(v.get("repo") or repo),
                "id": str(v.get("id") or ident),
                "label": str(v.get("label") or _label_for_priority(rank)),
                "ts": str(v.get("ts") or ""),
            }
        else:
            try:
                rank = int(v)
            except (TypeError, ValueError):
                rank = DEFAULT_PRIORITY
            cleaned[key] = {
                "rank": max(1, rank),
                "repo": repo,
                "id": ident,
                "label": _label_for_priority(rank),
                "ts": "",
            }
    return cleaned


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
    doc["items"] = _clean_items(doc.get("items"))
    try:
        doc["item_seq"] = int(doc.get("item_seq") or 0)
    except (TypeError, ValueError):
        doc["item_seq"] = 0
    doc["strict"] = bool(doc.get("strict"))
    return doc


def save_focus(home: Path, doc: dict[str, Any]) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = focus_path(home)
    payload = {
        "v": int(doc.get("v") or FOCUS_VERSION),
        "repos": dict(doc.get("repos") or {}),
        "items": dict(doc.get("items") or {}),
        "item_seq": int(doc.get("item_seq") or 0),
        "strict": bool(doc.get("strict")),
        "updated": doc.get("updated") or _utc_now(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def is_strict_focus(home: Path) -> bool:
    """FR #141: when True, assign/list only focused items/repos."""
    return bool(load_focus(home).get("strict"))


def set_strict_focus(home: Path, enabled: bool) -> bool:
    """Persist strict mode; returns the new value."""
    doc = load_focus(home)
    doc["strict"] = bool(enabled)
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return bool(doc["strict"])


def row_matches_focus(home: Path, row: dict[str, Any]) -> bool:
    """True if row is item-focused or its repo is in focus.json repos."""
    if item_rank_for_row(home, row) is not None:
        return True
    return repo_priority(home, str(row.get("repo") or "")) is not None


def filter_strict_focus_rows(home: Path, rows: list[dict]) -> list[dict]:
    """When strict is on, keep only focused rows; when off, return rows unchanged."""
    if not is_strict_focus(home):
        return list(rows)
    return [r for r in rows if isinstance(r, dict) and row_matches_focus(home, r)]


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


def _norm_id(ident: str) -> str:
    s = str(ident or "").strip()
    if not s:
        return ""
    return s if s.startswith("#") else f"#{s}"


def item_focus_map(home: Path) -> dict[str, dict[str, Any]]:
    return dict(load_focus(home).get("items") or {})


def item_rank_for_row(home: Path, row: dict[str, Any]) -> int | None:
    """Return item focus rank if this queue row is item-focused."""
    items = item_focus_map(home)
    if not items:
        return None
    repo = str(row.get("repo") or "").strip()
    ident = _norm_id(str(row.get("id") or ""))
    if not repo or not ident:
        return None
    repo_l = repo.lower()
    short_l = repo.rsplit("/", 1)[-1].lower()
    id_n = ident.lstrip("#")
    for key, meta in items.items():
        parsed = normalize_item_ref(key)
        if not parsed:
            continue
        k_repo, k_id, _ = parsed
        k_repo_l = k_repo.lower()
        k_short = k_repo.rsplit("/", 1)[-1].lower()
        if _norm_id(k_id).lstrip("#") != id_n:
            continue
        if k_repo_l == repo_l or ("/" not in k_repo and k_short == short_l):
            return int(meta.get("rank") or DEFAULT_PRIORITY)
        if "/" in k_repo and k_short == short_l and "/" not in repo:
            return int(meta.get("rank") or DEFAULT_PRIORITY)
    return None


def sort_unaccepted_rows(home: Path, rows: list[dict]) -> list[dict]:
    """
    Single sort for !list and !bored (FR #113 / #141):
    0) purge closed item focus; when strict, drop unfocused rows
    1) item-focused rows by rank ascending
    2) else repo focus priority ascending
    3) then seq ascending
    """
    purge_stale_item_focus(home)
    rows = filter_strict_focus_rows(home, list(rows))
    decorated = []
    for r in rows:
        repo = str(r.get("repo") or "")
        seq = int(r.get("seq") or 0)
        ir = item_rank_for_row(home, r)
        if ir is not None:
            decorated.append((0, ir, seq, r))
        else:
            decorated.append((1, sort_rank(home, repo), seq, r))
    decorated.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in decorated]


def set_item_focus(
    home: Path,
    item_token: str,
    *,
    rank: int | None = None,
    label: str | None = None,
) -> tuple[str, str | None, int]:
    """Focus a single issue/PR. Returns (status, canonical_key, rank)."""
    parsed = normalize_item_ref(item_token)
    if not parsed:
        return "bad", None, 0
    repo, ident, key = parsed
    doc = load_focus(home)
    items = dict(doc.get("items") or {})
    # drop case-insensitive duplicate keys
    key_l = key.lower()
    items = {k: v for k, v in items.items() if k.lower() != key_l}
    if rank is None:
        seq = int(doc.get("item_seq") or 0) + 1
        doc["item_seq"] = seq
        rk = seq
    else:
        rk = max(1, int(rank))
    lab = label or _label_for_priority(rk if rk in NAMED_PRIORITY.values() else rk)
    if rk in NAMED_PRIORITY.values():
        lab = label or _label_for_priority(rk)
    elif label:
        lab = label
    else:
        lab = str(rk)
    items[key] = {
        "rank": rk,
        "repo": repo,
        "id": ident,
        "label": lab,
        "ts": _utc_now(),
    }
    doc["items"] = items
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return "set", key, rk


def remove_item_focus(home: Path, item_token: str) -> tuple[str, str | None]:
    parsed = normalize_item_ref(item_token)
    if not parsed:
        return "bad", None
    repo, ident, key = parsed
    doc = load_focus(home)
    items = dict(doc.get("items") or {})
    short = repo.rsplit("/", 1)[-1].lower()
    id_n = ident.lstrip("#")
    kept: dict[str, Any] = {}
    removed = False
    for k, v in items.items():
        p = normalize_item_ref(k)
        if not p:
            kept[k] = v
            continue
        kr, kid, _ = p
        if kid.lstrip("#") == id_n and (
            kr.lower() == repo.lower() or kr.rsplit("/", 1)[-1].lower() == short
        ):
            removed = True
            continue
        kept[k] = v
    if not removed:
        return "missing", key
    doc["items"] = kept
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return "removed", key


def purge_stale_item_focus(home: Path) -> list[str]:
    """Drop item focus when the issue/PR is closed or merged (present in done).

    Absence from unaccepted alone is not enough — simon may focus before enqueue.
    """
    from .queue import load_queue

    doc = load_focus(home)
    items = dict(doc.get("items") or {})
    if not items:
        return []
    q = load_queue(home)
    done_ids: set[tuple[str, str]] = set()
    for row in q.get("done") or []:
        if not isinstance(row, dict):
            continue
        repo = str(row.get("repo") or "").strip().lower()
        ident = _norm_id(str(row.get("id") or "")).lstrip("#")
        if not repo or not ident:
            continue
        result = str(row.get("result") or "").lower()
        # Treat any done row as terminal for item focus (closed/merged/ok).
        done_ids.add((repo, ident))
        done_ids.add((repo.rsplit("/", 1)[-1], ident))
        _ = result  # reserved for finer filters later
    # Also: still live in unaccepted/accepted → keep even if also in done history
    live: set[tuple[str, str]] = set()
    for bucket in ("unaccepted", "accepted"):
        for row in q.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            repo = str(row.get("repo") or "").strip().lower()
            ident = _norm_id(str(row.get("id") or "")).lstrip("#")
            if repo and ident:
                live.add((repo, ident))
                live.add((repo.rsplit("/", 1)[-1], ident))
    removed: list[str] = []
    kept: dict[str, Any] = {}
    for k, v in items.items():
        parsed = normalize_item_ref(k)
        if not parsed:
            removed.append(k)
            continue
        repo, ident, key = parsed
        repo_l = repo.lower()
        short = repo.rsplit("/", 1)[-1].lower()
        id_n = ident.lstrip("#")
        if (repo_l, id_n) in live or (short, id_n) in live:
            kept[key] = v
            continue
        if (repo_l, id_n) in done_ids or (short, id_n) in done_ids:
            removed.append(key)
            continue
        kept[key] = v
    if removed:
        doc["items"] = kept
        doc["updated"] = _utc_now()
        save_focus(home, doc)
    return removed


def retarget_item_focus(
    home: Path,
    repo: str,
    old_id: str,
    *,
    new_id: str,
) -> str | None:
    """Move item focus from FR #old to MRB/UAT #new (same repo). Returns new key or None."""
    doc = load_focus(home)
    items = dict(doc.get("items") or {})
    old = _norm_id(old_id)
    new = _norm_id(new_id)
    repo_s = (repo or "").strip()
    if not repo_s or not old or not new:
        return None
    repo_l = repo_s.lower()
    short = repo_s.rsplit("/", 1)[-1].lower()
    old_n = old.lstrip("#")
    hit_key = None
    hit_meta = None
    for k, v in list(items.items()):
        parsed = normalize_item_ref(k)
        if not parsed:
            continue
        kr, kid, _ = parsed
        if kid.lstrip("#") != old_n:
            continue
        if kr.lower() == repo_l or kr.rsplit("/", 1)[-1].lower() == short:
            hit_key = k
            hit_meta = dict(v)
            break
    if not hit_key or hit_meta is None:
        return None
    items.pop(hit_key, None)
    new_key = f"{repo_s}#{new.lstrip('#')}"
    # drop any existing new_key
    items = {k: v for k, v in items.items() if k.lower() != new_key.lower()}
    hit_meta["repo"] = repo_s
    hit_meta["id"] = new
    hit_meta["ts"] = _utc_now()
    items[new_key] = hit_meta
    doc["items"] = items
    doc["updated"] = _utc_now()
    save_focus(home, doc)
    return new_key


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
    """Sorted (repo, priority, label) for !focus bare list (repos only)."""
    m = focus_map(home)
    rows = [(k, int(v.get("priority") or DEFAULT_PRIORITY), str(v.get("label") or "")) for k, v in m.items()]
    rows.sort(key=lambda t: (t[1], t[0].lower()))
    return rows


def list_item_focus_entries(home: Path) -> list[tuple[str, int, str]]:
    """Sorted (item_key, rank, label); purges stale first."""
    purge_stale_item_focus(home)
    m = item_focus_map(home)
    rows = [(k, int(v.get("rank") or DEFAULT_PRIORITY), str(v.get("label") or "")) for k, v in m.items()]
    rows.sort(key=lambda t: (t[1], t[0].lower()))
    return rows


def format_focus_lines(home: Path) -> list[str]:
    items = list_item_focus_entries(home)
    repos = list_focus_entries(home)
    strict = is_strict_focus(home)
    out: list[str] = [f"focus strict: {'on' if strict else 'off'}"]
    if not items and not repos:
        out.append("focus: (none)")
        return out
    if items:
        out.append(f"focus items ({len(items)}):")
        for key, rk, lab in items:
            tag = lab if lab in NAMED_PRIORITY else str(rk)
            out.append(f"  {rk} ({tag}) {key}")
    if repos:
        out.append(f"focus repos ({len(repos)}):")
        for repo, pr, lab in repos:
            tag = lab if lab in NAMED_PRIORITY else str(pr)
            out.append(f"  {pr} ({tag}) {repo}")
    return out


def handle_focus_cmd(home: Path, arg: str) -> list[str]:
    """arg is remainder after !focus (may be empty)."""
    raw = (arg or "").strip()
    if not raw:
        return format_focus_lines(home)
    parts = raw.split()

    # FR #141: !focus strict on|off
    if parts and parts[0].lower() == "strict":
        if len(parts) == 1:
            return [f"focus strict: {'on' if is_strict_focus(home) else 'off'}"]
        flag = parts[1].lower()
        if flag in ("on", "1", "true", "yes"):
            set_strict_focus(home, True)
            return ["focus strict: on"]
        if flag in ("off", "0", "false", "no"):
            set_strict_focus(home, False)
            return ["focus strict: off"]
        return ["focus: usage !focus strict on|off"]

    def _try_item(tok: str, rank: int | None, lab: str | None) -> list[str] | None:
        if normalize_item_ref(tok) is None:
            return None
        st, canon, rk = set_item_focus(home, tok, rank=rank, label=lab)
        if st == "bad":
            return ["focus: bad item (use owner/repo#N or repo#N)"]
        return [f"focus: item {canon} rank={rk}"]

    if len(parts) == 1:
        item_try = _try_item(parts[0], None, "high")
        if item_try is not None:
            return item_try
        st, canon, pr = set_focus(home, parts[0], priority=DEFAULT_PRIORITY, label="high")
        if st == "bad":
            return ["focus: bad repo/item (use owner/name, owner/repo#N, or URL)"]
        return [f"focus: {canon} priority={pr} (high)"]

    # first token priority/rank?
    pri = parse_priority_token(parts[0])
    if pri is not None:
        pr, lab = pri
        rest = " ".join(parts[1:]).strip()
        if not rest:
            return ["focus: usage !focus [n|high|medium|low] {repo|repo#N}"]
        item_try = _try_item(rest, pr, lab)
        if item_try is not None:
            return item_try
        st, canon, pr2 = set_focus(home, rest, priority=pr, label=lab)
        if st == "bad":
            return ["focus: bad repo (use owner/name or URL)"]
        return [f"focus: {canon} priority={pr2} ({lab})"]

    # item then optional rank: owner/repo#N 3
    if len(parts) >= 2 and normalize_item_ref(parts[0]) is not None:
        pri2 = parse_priority_token(parts[1])
        if pri2 is not None:
            pr, lab = pri2
            return _try_item(parts[0], pr, lab) or ["focus: bad item"]
        # ignore trailing junk — still set item
        return _try_item(parts[0], None, "high") or ["focus: bad item"]

    # multi-token repo URL without priority
    item_try = _try_item(raw, None, "high")
    if item_try is not None:
        return item_try
    st, canon, pr = set_focus(home, raw, priority=DEFAULT_PRIORITY, label="high")
    if st == "bad":
        return ["focus: usage !focus [n|high|medium|low] {repo|owner/repo#N}"]
    return [f"focus: {canon} priority={pr} (high)"]


def handle_unfocus_cmd(home: Path, arg: str) -> list[str]:
    raw = (arg or "").strip()
    if not raw:
        return ["unfocus: usage !unfocus {repo|repo#N}|all"]
    if normalize_item_ref(raw) is not None:
        st, canon = remove_item_focus(home, raw)
        if st == "bad":
            return ["unfocus: bad item"]
        if st == "missing":
            return [f"unfocus: {canon} was not focused"]
        return [f"unfocus: removed {canon}"]
    st, canon = remove_focus(home, raw)
    if st == "bad":
        return ["unfocus: bad repo"]
    if st == "cleared":
        # also clear items on !unfocus all
        doc = load_focus(home)
        n_items = len(doc.get("items") or {})
        doc["items"] = {}
        doc["item_seq"] = 0
        doc["updated"] = _utc_now()
        save_focus(home, doc)
        return [f"unfocus: cleared {canon} repo + {n_items} item entries"]
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
    """Digest-safe list: items first, then repos.

    Strict mode is exposed separately as digest ``focus_strict`` (FR #154) so
    existing list consumers keep the same shape.
    """
    out: list[dict[str, Any]] = []
    for key, rk, lab in list_item_focus_entries(home):
        out.append({"item": key, "rank": rk, "label": lab, "kind": "item"})
    for r, p, lab in list_focus_entries(home):
        out.append({"repo": r, "priority": p, "label": lab, "kind": "repo"})
    return out


def owner_account_name() -> str:
    """FR #107: Ergo services account that may !focus / !ignore / !sweep (env override)."""
    import os

    raw = (os.environ.get("JEEVES_OWNER_ACCOUNT") or "simon").strip().lower()
    return raw or "simon"


def may_mutate_focus(nick: str, *, account: str | None = None, mode_grants_live: bool = False) -> bool:
    """Only owner nick (simon / simon-*) with matching services account when mode_grants live."""
    n = (nick or "").strip().lower()
    owner = owner_account_name()
    if n != owner and not n.startswith(f"{owner}-"):
        return False
    if mode_grants_live:
        return (account or "").strip().lower() == owner
    return True
