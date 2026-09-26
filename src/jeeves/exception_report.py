"""FR #148: deterministic exception → GitHub issue (no LLM).

Catch-all reporting for Jeeves deterministic paths: template, dedupe, rate
limit, local spool + retry. Scripts only.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__ as PKG_VERSION
from .intake import GitHubDown

DEFAULT_REPO = "SimonBarnett/gh-Jeeves"
AUTO_LABEL = "auto-exception"
DEFAULT_DEDUPE_WINDOW_S = 24 * 3600
DEFAULT_RATE_MAX = 10
DEFAULT_RATE_WINDOW_S = 3600
SPOOL_DIRNAME = "exception_spool"
INDEX_NAME = "exception_report_index.json"


class ExceptionFiler(Protocol):
    def create_issue(
        self, repo: str, title: str, body: str, labels: list[str]
    ) -> dict[str, Any]: ...

    def comment_issue(self, repo: str, number: int, body: str) -> dict[str, Any]: ...


@dataclass
class FakeExceptionFiler:
    """In-memory filer for tests (also satisfies intake.create_issue shape)."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False
    _n: int = 2000

    def create_issue(
        self, repo: str, title: str, body: str, labels: list[str]
    ) -> dict[str, Any]:
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

    def comment_issue(self, repo: str, number: int, body: str) -> dict[str, Any]:
        if self.down:
            raise GitHubDown("github unreachable")
        row = {"repo": repo, "number": number, "body": body}
        self.comments.append(row)
        return {"ok": True}


@dataclass
class UrllibExceptionFiler:
    """Live GitHub Issues API via urllib (token from env / file)."""

    token: str
    api_base: str = "https://api.github.com"
    timeout_s: float = 30.0

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = self.api_base.rstrip("/") + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"gh-Jeeves-exception-report/{PKG_VERSION}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise GitHubDown(f"HTTP {e.code}: {body[:400]}") from e
        except URLError as e:
            raise GitHubDown(str(e)) from e

    def create_issue(
        self, repo: str, title: str, body: str, labels: list[str]
    ) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        doc = self._request(
            "POST",
            f"/repos/{owner}/{name}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return {
            "url": doc.get("html_url") or f"https://github.com/{repo}/issues/{doc.get('number')}",
            "number": int(doc["number"]),
        }

    def comment_issue(self, repo: str, number: int, body: str) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        self._request(
            "POST",
            f"/repos/{owner}/{name}/issues/{int(number)}/comments",
            {"body": body},
        )
        return {"ok": True}


def resolve_github_token() -> str | None:
    for key in ("JEEVES_EXCEPTION_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    for cand in (
        Path(os.environ.get("JEEVES_HOME") or "") / "github.token",
        Path(os.environ.get("BOB_DIGEST_HOME") or "") / "github.token",
        Path.home() / ".agentic-irc-jeeves" / "github.token",
    ):
        try:
            if cand.is_file():
                t = cand.read_text(encoding="utf-8").strip()
                if t:
                    return t
        except OSError:
            continue
    return None


def default_filer() -> ExceptionFiler | None:
    tok = resolve_github_token()
    if not tok:
        return None
    return UrllibExceptionFiler(token=tok)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint_message(msg: str, *, max_len: int = 120) -> str:
    s = " ".join((msg or "").split())
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def dedupe_key(
    exc_type: str,
    location: str,
    message: str,
) -> str:
    raw = f"{exc_type}|{location}|{fingerprint_message(message)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def format_location(tb: traceback.StackSummary | list[traceback.FrameSummary]) -> str:
    frames = list(tb)
    if not frames:
        return "<unknown>"
    last = frames[-1]
    return f"{last.filename}:{last.lineno}:{last.name}"


def build_exception_record(
    exc: BaseException,
    *,
    component: str = "jeeves",
    host: str | None = None,
    version: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    tb_list = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    loc = format_location(tb_list)
    et = type(exc).__name__
    msg = str(exc) or et
    key = dedupe_key(et, loc, msg)
    return {
        "v": 1,
        "id": str(uuid.uuid4()),
        "dedupe_key": key,
        "ts_utc": now or _utc_now(),
        "exc_type": et,
        "message": msg,
        "location": loc,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "component": component,
        "host": host or socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "jeeves_version": version or PKG_VERSION,
        "machine": os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or socket.gethostname(),
    }


def render_issue_title(rec: dict[str, Any]) -> str:
    loc = str(rec.get("location") or "")
    base = Path(loc.split(":")[0]).name if loc else "unknown"
    msg = fingerprint_message(str(rec.get("message") or ""), max_len=60)
    return f"[auto-exception] {rec.get('exc_type')}: {msg} @ {base}"


def render_issue_body(rec: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## Auto-filed exception (FR #148)",
            "",
            "Deterministic report — **no LLM**.",
            "",
            f"- **Type:** `{rec.get('exc_type')}`",
            f"- **Message:** {rec.get('message')}",
            f"- **Location:** `{rec.get('location')}`",
            f"- **Component:** `{rec.get('component')}`",
            f"- **Timestamp (UTC):** {rec.get('ts_utc')}",
            f"- **Host:** {rec.get('host')}",
            f"- **Machine:** {rec.get('machine')}",
            f"- **OS:** {rec.get('os')}",
            f"- **Jeeves version:** {rec.get('jeeves_version')}",
            f"- **Dedupe key:** `{rec.get('dedupe_key')}`",
            "",
            "### Traceback",
            "",
            "```",
            str(rec.get("traceback") or "").rstrip(),
            "```",
            "",
            f"<!-- jeeves-exception-dedupe:{rec.get('dedupe_key')} -->",
        ]
    )


def spool_dir(home: Path) -> Path:
    return Path(home) / SPOOL_DIRNAME


def index_path(home: Path) -> Path:
    return Path(home) / INDEX_NAME


def load_index(home: Path) -> dict[str, Any]:
    p = index_path(home)
    if not p.is_file():
        return {"v": 1, "keys": {}, "rate": []}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return {"v": 1, "keys": {}, "rate": []}
        doc.setdefault("keys", {})
        doc.setdefault("rate", [])
        return doc
    except (OSError, json.JSONDecodeError):
        return {"v": 1, "keys": {}, "rate": []}


def save_index(home: Path, doc: dict[str, Any]) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    p = index_path(home)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def write_spool(home: Path, rec: dict[str, Any]) -> Path:
    d = spool_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{rec.get('ts_utc', 'x').replace(':', '')}_{rec.get('id')}.json"
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return path


def list_spool(home: Path) -> list[Path]:
    d = spool_dir(home)
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


@dataclass
class ExceptionReporter:
    home: Path
    filer: ExceptionFiler | None = None
    repo: str = DEFAULT_REPO
    dedupe_window_s: float = DEFAULT_DEDUPE_WINDOW_S
    rate_max: int = DEFAULT_RATE_MAX
    rate_window_s: float = DEFAULT_RATE_WINDOW_S
    label: str = AUTO_LABEL
    now_fn: Callable[[], float] = time.time

    def report(
        self,
        exc: BaseException,
        *,
        component: str = "jeeves",
    ) -> dict[str, Any]:
        """Report exception: create/comment issue or spool. Never raises outward."""
        try:
            return self._report(exc, component=component)
        except Exception as inner:  # noqa: BLE001 — reporting must not raise
            try:
                log = Path(self.home) / "exception_report_failures.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as f:
                    f.write(
                        f"{_utc_now()} report_failed {type(inner).__name__}: {inner}\n"
                    )
            except OSError:
                pass
            return {"ok": False, "error": f"{type(inner).__name__}: {inner}"}

    def _report(self, exc: BaseException, *, component: str) -> dict[str, Any]:
        rec = build_exception_record(exc, component=component)
        return self._report_record(rec)

    def _report_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        home = Path(self.home)
        home.mkdir(parents=True, exist_ok=True)
        key = str(rec["dedupe_key"])
        now = float(self.now_fn())
        idx = load_index(home)

        # Rate limit
        rate = [float(t) for t in (idx.get("rate") or []) if now - float(t) <= self.rate_window_s]
        if len(rate) >= self.rate_max:
            path = write_spool(home, {**rec, "spool_reason": "rate_limit"})
            idx["rate"] = rate
            save_index(home, idx)
            return {"ok": True, "action": "rate_limited_spool", "spool": str(path), "dedupe_key": key}

        keys = dict(idx.get("keys") or {})
        prev = keys.get(key)
        filer = self.filer if self.filer is not None else default_filer()
        if filer is None:
            path = write_spool(home, {**rec, "spool_reason": "no_token"})
            return {"ok": True, "action": "spooled_no_token", "spool": str(path), "dedupe_key": key}

        title = render_issue_title(rec)
        body = render_issue_body(rec)
        try:
            if (
                isinstance(prev, dict)
                and prev.get("number")
                and (now - float(prev.get("ts") or 0)) <= self.dedupe_window_s
            ):
                num = int(prev["number"])
                filer.comment_issue(
                    self.repo,
                    num,
                    f"Repeat at {rec['ts_utc']} (dedupe `{key}`)\n\n```\n{rec.get('message')}\n```",
                )
                keys[key] = {"number": num, "ts": now, "url": prev.get("url")}
                rate.append(now)
                idx["keys"] = keys
                idx["rate"] = rate
                save_index(home, idx)
                return {
                    "ok": True,
                    "action": "commented",
                    "number": num,
                    "url": prev.get("url"),
                    "dedupe_key": key,
                }

            created = filer.create_issue(
                self.repo, title, body, labels=[self.label]
            )
            num = int(created["number"])
            url = created.get("url")
            keys[key] = {"number": num, "ts": now, "url": url}
            rate.append(now)
            idx["keys"] = keys
            idx["rate"] = rate
            save_index(home, idx)
            return {
                "ok": True,
                "action": "created",
                "number": num,
                "url": url,
                "dedupe_key": key,
            }
        except GitHubDown as e:
            path = write_spool(home, {**rec, "spool_reason": f"github_down:{e}"})
            return {
                "ok": True,
                "action": "spooled_github_down",
                "spool": str(path),
                "dedupe_key": key,
                "error": str(e),
            }

    def drain_spool(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Retry spooled records. Returns per-file results."""
        out: list[dict[str, Any]] = []
        for path in list_spool(self.home)[:limit]:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                out.append({"ok": False, "spool": str(path), "error": str(e)})
                continue
            # clear spool_reason noise for re-report
            rec.pop("spool_reason", None)
            result = self._report_record(rec)
            if result.get("action") in ("created", "commented"):
                try:
                    path.unlink()
                except OSError:
                    pass
            out.append({**result, "spool": str(path)})
        return out


def install_sys_excepthook(reporter: ExceptionReporter) -> None:
    """Top-level hook: report then chain to previous hook."""
    prev = sys.__excepthook__

    def _hook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        try:
            if isinstance(exc, BaseException):
                reporter.report(exc, component="sys.excepthook")
        finally:
            prev(exc_type, exc, tb)

    sys.excepthook = _hook  # type: ignore[assignment]


def guarded_main(run: Callable[[], int], *, home: Path, component: str = "main") -> int:
    """Wrap a deterministic entry so any exception is reported then exits 1."""
    reporter = ExceptionReporter(home=home)
    install_sys_excepthook(reporter)
    try:
        return int(run())
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else (0 if code is None else 1)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        reporter.report(exc, component=component)
        print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI: drain spool or report a deliberate test exception."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m jeeves.exception_report")
    p.add_argument("--home", default=os.environ.get("JEEVES_HOME") or "")
    p.add_argument("--drain", action="store_true", help="Retry local exception spool")
    p.add_argument(
        "--test-raise",
        action="store_true",
        help="Raise a deliberate exception to exercise the reporter (tests / ops)",
    )
    p.add_argument("--repo", default=DEFAULT_REPO)
    args = p.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else Path.home() / ".agentic-irc-jeeves"
    reporter = ExceptionReporter(home=home, repo=args.repo)
    if args.drain:
        results = reporter.drain_spool()
        print(json.dumps(results, indent=2))
        return 0
    if args.test_raise:
        try:
            raise RuntimeError("FR148 deliberate test exception")
        except RuntimeError as e:
            print(json.dumps(reporter.report(e, component="cli-test"), indent=2))
            return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())