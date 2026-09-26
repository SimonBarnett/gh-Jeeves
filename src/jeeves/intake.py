"""No-GitHub webhook intake (FR #26).

POST /bob/v1/intake accepts issues/FRs/skill harvests from machines without `gh`.
Service credential files on GitHub; zero AI tokens. Scripts only.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

KINDS = frozenset({"issue", "fr", "skill", "harvest"})
MAX_BODY_BYTES = 256 * 1024
MAX_FILES = 32
MAX_FILE_BYTES = 128 * 1024
DEFAULT_RATE_PER_MIN = 30
DEFAULT_ALLOW_REPOS = frozenset(
    {
        "SimonBarnett/gh-Jeeves",
        "SimonBarnett/agentic_irc",
        "SimonBarnett/agentic_build",
        "SimonBarnett/skills-visionary",
        "SimonBarnett/AgentMonitor",
    }
)
_SECRETISH = re.compile(
    r"(?i)(password\s*=\s*\S+|api[_-]?key\s*=\s*\S+|ghp_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9]{10,}|xox[baprs]-[A-Za-z0-9-]+|bearer\s+\S{8,})"
)
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubFiler(Protocol):
    """Service credential surface (fake in tests)."""

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Return {url, number}."""

    def create_draft_pr(
        self,
        repo: str,
        title: str,
        body: str,
        branch: str,
        files: list[dict[str, str]],
        labels: list[str],
    ) -> dict[str, Any]:
        """Return {url, number, branch}. May raise GitHubDown."""


class GitHubDown(RuntimeError):
    pass


@dataclass
class FakeGitHubFiler:
    """In-memory GitHub for G1 / FR #26 tests."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    prs: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False
    _n: int = 1000

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        if self.down:
            raise GitHubDown("github unreachable")
        self._n += 1
        row = {
            "repo": repo,
            "number": self._n,
            "title": title,
            "body": body,
            "labels": list(labels),
            "url": f"https://github.com/{repo}/issues/{self._n}",
        }
        self.issues.append(row)
        return {"url": row["url"], "number": self._n}

    def create_draft_pr(
        self,
        repo: str,
        title: str,
        body: str,
        branch: str,
        files: list[dict[str, str]],
        labels: list[str],
    ) -> dict[str, Any]:
        if self.down:
            raise GitHubDown("github unreachable")
        self._n += 1
        row = {
            "repo": repo,
            "number": self._n,
            "title": title,
            "body": body,
            "branch": branch,
            "files": list(files),
            "labels": list(labels),
            "draft": True,
            "url": f"https://github.com/{repo}/pull/{self._n}",
        }
        self.prs.append(row)
        return {"url": row["url"], "number": self._n, "branch": branch}


@dataclass
class IntakeConfig:
    allow_repos: frozenset[str] = DEFAULT_ALLOW_REPOS
    rate_per_min: int = DEFAULT_RATE_PER_MIN
    max_body_bytes: int = MAX_BODY_BYTES
    fleet_key: str | None = None  # optional X-Bob-Intake-Key
    require_key: bool = False


@dataclass
class IntakeResult:
    status: int
    body: dict[str, Any]
    log_safe: str = ""  # never contains secrets/contact


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def intake_root(home: Path) -> Path:
    p = Path(home) / "intake"
    p.mkdir(parents=True, exist_ok=True)
    (p / "outbox").mkdir(parents=True, exist_ok=True)
    (p / "records").mkdir(parents=True, exist_ok=True)
    return p


def _redact(text: str) -> str:
    return _SECRETISH.sub("[redacted]", text or "")


def _byte_len(s: str) -> int:
    return len((s or "").encode("utf-8"))


def payload_size_bytes(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def validate_payload(
    payload: dict,
    *,
    cfg: IntakeConfig | None = None,
    keyed: bool = False,
) -> tuple[str | None, dict[str, Any]]:
    """Return (error, normalized). error set → 4xx."""
    cfg = cfg or IntakeConfig()
    if not isinstance(payload, dict):
        return "malformed", {}
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return "bad_kind", {}
    repo = str(payload.get("repo") or "").strip()
    if not _REPO_RE.fullmatch(repo):
        return "bad_repo", {}
    if repo not in cfg.allow_repos:
        return "repo_not_allowed", {}
    title = str(payload.get("title") or "").strip()
    if not title or len(title) > 200:
        return "bad_title", {}
    body = str(payload.get("body") or "")
    files = payload.get("files") or []
    if not isinstance(files, list):
        return "bad_files", {}
    if len(files) > MAX_FILES:
        return "too_many_files", {}
    norm_files: list[dict[str, str]] = []
    file_bytes = 0
    for f in files:
        if not isinstance(f, dict):
            return "bad_files", {}
        path = str(f.get("path") or "").strip().replace("\\", "/")
        content = str(f.get("content") or "")
        if not path or ".." in path.split("/") or path.startswith("/"):
            return "bad_file_path", {}
        cb = _byte_len(content)
        if cb > MAX_FILE_BYTES:
            return "file_too_large", {}
        file_bytes += cb
        norm_files.append({"path": path, "content": content})
    total = _byte_len(title) + _byte_len(body) + file_bytes
    if total > cfg.max_body_bytes or payload_size_bytes(payload) > cfg.max_body_bytes + 4096:
        return "payload_too_large", {}
    if kind in ("skill", "harvest") and not norm_files and not body.strip():
        return "empty_harvest", {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    contact = str(payload.get("contact") or "").strip()
    contact_public = bool(payload.get("contact_public"))
    idem = str(payload.get("idempotency_key") or "").strip()
    if len(idem) > 128:
        return "bad_idempotency_key", {}
    norm = {
        "kind": kind,
        "repo": repo,
        "title": title,
        "body": body,
        "files": norm_files,
        "source": {
            "machine": str(source.get("machine") or "")[:64],
            "agent": str(source.get("agent") or "")[:64],
            "skill_book": str(source.get("skill_book") or "")[:64],
            "version": str(source.get("version") or "")[:32],
        },
        "contact": contact[:200] if contact else "",
        "contact_public": contact_public,
        "idempotency_key": idem,
        "keyed": keyed,
    }
    return None, norm


class RateLimiter:
    def __init__(self, per_min: int = DEFAULT_RATE_PER_MIN) -> None:
        self.per_min = max(1, int(per_min))
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        t = float(now if now is not None else time.time())
        bucket = self._hits.setdefault(key, [])
        cutoff = t - 60.0
        self._hits[key] = [x for x in bucket if x >= cutoff]
        if len(self._hits[key]) >= self.per_min:
            return False
        self._hits[key].append(t)
        return True


def _provenance_footer(norm: dict, intake_id: str, *, quarantine: bool) -> str:
    src = norm.get("source") or {}
    bits = [
        "",
        "---",
        f"_via-intake id=`{intake_id}` ts=`{_utc()}`_",
        f"_source machine=`{src.get('machine') or '-'}` agent=`{src.get('agent') or '-'}` "
        f"book=`{src.get('skill_book') or '-'}` ver=`{src.get('version') or '-'}`_",
    ]
    if quarantine:
        bits.append("_quarantine: unkeyed source — triage before FR queue_")
    if norm.get("contact_public") and norm.get("contact"):
        bits.append(f"_contact: {_redact(str(norm['contact']))}_")
    return "\n".join(bits)


def _labels_for(norm: dict, *, quarantine: bool) -> list[str]:
    labels = ["via-intake"]
    kind = norm["kind"]
    if kind == "fr":
        labels.append("feature-request")
    if kind in ("skill", "harvest"):
        labels.append("skill")
    if kind in ("issue", "fr"):
        # FR #151: agent/intake proposals await human vision fit (MRB #1)
        labels.append("needs-mrb1")
    if quarantine:
        labels.append("via-intake-untriaged")
    return labels


def _record_path(home: Path, intake_id: str) -> Path:
    return intake_root(home) / "records" / f"{intake_id}.json"


def _idem_path(home: Path, key: str) -> Path:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return intake_root(home) / "records" / f"idem-{h}.json"


def load_record(home: Path, intake_id: str) -> dict | None:
    p = _record_path(home, intake_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_record(home: Path, rec: dict) -> None:
    intake_id = str(rec["intake_id"])
    path = _record_path(home, intake_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    idem = str(rec.get("idempotency_key") or "")
    if idem:
        ip = _idem_path(home, idem)
        ip.write_text(json.dumps({"intake_id": intake_id}) + "\n", encoding="utf-8")


def _find_idempotent(home: Path, key: str) -> dict | None:
    if not key:
        return None
    ip = _idem_path(home, key)
    if not ip.is_file():
        return None
    try:
        meta = json.loads(ip.read_text(encoding="utf-8"))
        return load_record(home, str(meta.get("intake_id") or ""))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_log(norm: dict, intake_id: str, msg: str) -> str:
    # never include contact or body/file content
    return (
        f"intake id={intake_id} kind={norm.get('kind')} repo={norm.get('repo')} "
        f"machine={((norm.get('source') or {}).get('machine') or '-')} {msg}"
    )


def file_submission(
    home: Path,
    norm: dict,
    filer: GitHubFiler,
    *,
    intake_id: str | None = None,
    quarantine: bool = False,
) -> dict[str, Any]:
    """File now or raise GitHubDown. Returns record dict."""
    iid = intake_id or f"in_{uuid.uuid4().hex[:16]}"
    labels = _labels_for(norm, quarantine=quarantine)
    body = _redact(str(norm.get("body") or ""))
    body = body + _provenance_footer(norm, iid, quarantine=quarantine)
    title = str(norm["title"])
    repo = str(norm["repo"])
    kind = norm["kind"]
    rec: dict[str, Any] = {
        "intake_id": iid,
        "kind": kind,
        "repo": repo,
        "title": title,
        "idempotency_key": norm.get("idempotency_key") or "",
        "quarantine": quarantine,
        "ts": _utc(),
        "state": "filing",
        "url": None,
        "queued": False,
    }
    try:
        if kind in ("issue", "fr"):
            out = filer.create_issue(repo, title, body, labels)
            rec["url"] = out["url"]
            rec["number"] = out["number"]
            rec["state"] = "filed"
        else:
            branch = f"intake/{iid}"
            files = list(norm.get("files") or [])
            try:
                out = filer.create_draft_pr(repo, title, body, branch, files, labels)
                rec["url"] = out["url"]
                rec["number"] = out["number"]
                rec["branch"] = out.get("branch")
                rec["state"] = "filed"
            except Exception:
                # fallback issue with file list (no raw huge dump if empty)
                listing = "\n".join(f"- `{f.get('path')}`" for f in files) or "- (no files)"
                issue_body = body + "\n\n### Files\n" + listing
                out = filer.create_issue(repo, title, issue_body, labels)
                rec["url"] = out["url"]
                rec["number"] = out["number"]
                rec["state"] = "filed_issue_fallback"
    except GitHubDown:
        rec["state"] = "queued"
        rec["queued"] = True
        # durable outbox (no contact in clear log fields)
        outbox = intake_root(home) / "outbox" / f"{iid}.json"
        safe_norm = dict(norm)
        safe_norm["contact"] = ""  # never persist contact in outbox by default
        if norm.get("contact_public") and norm.get("contact"):
            safe_norm["contact"] = _redact(str(norm["contact"]))
        outbox.write_text(
            json.dumps({"norm": safe_norm, "intake_id": iid, "quarantine": quarantine}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        _save_record(home, rec)
        raise
    _save_record(home, rec)
    return rec


def process_intake(
    home: Path,
    payload: dict,
    *,
    filer: GitHubFiler,
    cfg: IntakeConfig | None = None,
    client_ip: str = "0.0.0.0",
    intake_key_header: str = "",
    rate: RateLimiter | None = None,
    now: float | None = None,
) -> IntakeResult:
    """Full POST handler logic. Returns HTTP status + JSON body."""
    cfg = cfg or IntakeConfig()
    rate = rate or RateLimiter(cfg.rate_per_min)
    key_ok = False
    if cfg.fleet_key:
        key_ok = (intake_key_header or "") == cfg.fleet_key
    if cfg.require_key and not key_ok:
        return IntakeResult(401, {"error": "unauthorized"}, log_safe="intake unauthorized")

    # rate limit by IP + source machine
    src_machine = ""
    if isinstance(payload, dict) and isinstance(payload.get("source"), dict):
        src_machine = str(payload["source"].get("machine") or "")
    rk = f"{client_ip}|{src_machine or '-'}"
    if not rate.allow(rk, now=now):
        return IntakeResult(429, {"error": "rate_limited"}, log_safe=f"intake rate_limited ip={client_ip}")

    err, norm = validate_payload(payload, cfg=cfg, keyed=key_ok)
    if err:
        code = 400
        if err == "repo_not_allowed":
            code = 403
        return IntakeResult(code, {"error": err}, log_safe=f"intake reject={err}")

    # idempotency
    existing = _find_idempotent(home, str(norm.get("idempotency_key") or ""))
    if existing and existing.get("url"):
        return IntakeResult(
            202,
            {
                "intake_id": existing["intake_id"],
                "url": existing["url"],
                "queued": False,
                "duplicate": True,
            },
            log_safe=_safe_log(norm, existing["intake_id"], "duplicate"),
        )
    if existing and existing.get("queued"):
        return IntakeResult(
            202,
            {"intake_id": existing["intake_id"], "queued": True, "duplicate": True},
            log_safe=_safe_log(norm, existing["intake_id"], "duplicate_queued"),
        )

    quarantine = not key_ok and bool(cfg.fleet_key)  # unkeyed when key configured
    if not cfg.fleet_key:
        quarantine = False  # open lab mode still labels via-intake only

    iid = f"in_{uuid.uuid4().hex[:16]}"
    try:
        rec = file_submission(home, norm, filer, intake_id=iid, quarantine=quarantine)
    except GitHubDown:
        rec = load_record(home, iid) or {
            "intake_id": iid,
            "queued": True,
            "state": "queued",
        }
        return IntakeResult(
            202,
            {"intake_id": iid, "queued": True},
            log_safe=_safe_log(norm, iid, "queued_github_down"),
        )

    return IntakeResult(
        202,
        {"intake_id": rec["intake_id"], "url": rec.get("url"), "queued": False},
        log_safe=_safe_log(norm, rec["intake_id"], f"filed state={rec.get('state')}"),
    )


def get_intake_status(home: Path, intake_id: str) -> tuple[int, dict[str, Any]]:
    rec = load_record(home, intake_id)
    if not rec:
        return 404, {"error": "not_found"}
    out = {
        "intake_id": rec.get("intake_id"),
        "state": rec.get("state"),
        "url": rec.get("url"),
        "queued": bool(rec.get("queued")),
        "repo": rec.get("repo"),
        "kind": rec.get("kind"),
    }
    return 200, out


def drain_intake_outbox(
    home: Path,
    filer: GitHubFiler,
    *,
    limit: int = 20,
) -> list[str]:
    """Retry queued filings when GitHub is back. Returns intake_ids filed."""
    root = intake_root(home) / "outbox"
    filed: list[str] = []
    for path in sorted(root.glob("*.json"))[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        norm = data.get("norm") or {}
        iid = str(data.get("intake_id") or path.stem)
        quarantine = bool(data.get("quarantine"))
        try:
            file_submission(home, norm, filer, intake_id=iid, quarantine=quarantine)
        except GitHubDown:
            continue
        path.unlink(missing_ok=True)
        filed.append(iid)
    return filed


def harvest_should_use_intake(*, gh_available: bool, gh_authenticated: bool) -> bool:
    """Route order: gh auth → gh; else intake (FR #26)."""
    if gh_available and gh_authenticated:
        return False
    return True
