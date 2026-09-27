#!/usr/bin/env python3
"""Deterministic Jeeves handoff-failure monitor (no IRC spam, no LLM).

Detects:
  - busy worker with no matching accepted row (ghost busy)
  - busy longer than N minutes (stale busy)
  - ACK without DONE within timeout (from service log)
  - dual assign of same row_key within offer window
  - ack_no_match storms
  - bored_skip reason=busy while unaccepted work exists and accepted empty for nick
  - seat outbox DONE not mirrored in chair log within lag window

Writes structured lines to a local alert log (default under Jeeves home).
Optional --loop for periodic scans. Never mutates queue/offers.

Usage:
  python tools/watch_jeeves_handoff.py --once
  python tools/watch_jeeves_handoff.py --loop 30
  python tools/watch_jeeves_handoff.py --once --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow `python tools/watch_jeeves_handoff.py` from repo root without PYTHONPATH.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_ASSIGN_RE = re.compile(
    r"event=assign\s+nick=(\S+)\s+line=\1:\s+(FR|MRB|UAT)\s+"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)",
    re.I,
)
_ACK_RE = re.compile(
    r"event=ack\s+nick=(\S+)\s+job=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s+mode=(\S+)",
    re.I,
)
_DONE_RE = re.compile(
    r"event=done\s+nick=(\S+)\s+job=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\s+mode=(\S+)",
    re.I,
)
_ACK_NO_MATCH_RE = re.compile(r"event=ack_no_match\s+nick=(\S+)", re.I)
_BORED_BUSY_RE = re.compile(
    r"event=bored_skip\s+nick=(\S+)\s+action=busy\s+reason=busy", re.I
)
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+")
_DONE_OUTBOX_RE = re.compile(
    r"^(?:PRIVMSG\s+\S+\s+:)?DONE\s+(FR|MRB|UAT)\s+"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b",
    re.I,
)


@dataclass
class Finding:
    kind: str
    severity: str
    detail: str
    nick: str = ""
    row_key: str = ""
    evidence: str = ""


@dataclass
class ScanResult:
    ts: str
    findings: list[Finding] = field(default_factory=list)
    workers_busy: int = 0
    accepted: int = 0
    unaccepted: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> float | None:
    s = (ts or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def default_digest_home() -> Path:
    env = (os.environ.get("BOB_DIGEST_HOME") or os.environ.get("JEEVES_DIGEST_HOME") or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agentic-irc-bobiverse"


def default_jeeves_home() -> Path:
    env = (os.environ.get("JEEVES_HOME") or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".agentic-irc-jeeves"


def default_service_log(jeeves_home: Path) -> Path:
    return Path(jeeves_home) / "bobjeeves-service.log"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def row_key(task: str, repo: str, number: str) -> str:
    t = (task or "").upper()
    if t == "PR":
        t = "FR"
    ident = str(number or "").strip()
    if ident and not ident.startswith("#"):
        ident = f"#{ident}"
    return f"{repo}|{t}|{ident}"


def _tail_lines(path: Path, max_bytes: int = 512_000) -> list[str]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()
            data = fh.read()
    except OSError:
        return []
    text = data.decode("utf-8", "replace")
    return [ln for ln in text.splitlines() if ln.strip()]


def scan_queue_state(
    digest_home: Path,
    *,
    busy_max_s: float,
    now: float | None = None,
) -> list[Finding]:
    now_f = time.time() if now is None else float(now)
    q = load_json(digest_home / "queue.json")
    findings: list[Finding] = []
    accepted = [r for r in (q.get("accepted") or []) if isinstance(r, dict)]
    accepted_by_nick: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        nick = str(row.get("nick") or "").strip()
        if nick:
            accepted_by_nick[nick].append(row)
    workers = q.get("workers") if isinstance(q.get("workers"), dict) else {}
    unaccepted = [r for r in (q.get("unaccepted") or []) if isinstance(r, dict)]

    for nick, ent in workers.items():
        if not isinstance(ent, dict):
            continue
        if str(ent.get("state") or "").lower() != "busy":
            continue
        rows = accepted_by_nick.get(str(nick), [])
        if not rows:
            findings.append(
                Finding(
                    kind="ghost_busy",
                    severity="error",
                    nick=str(nick),
                    detail=f"worker state=busy with no accepted row (unaccepted={len(unaccepted)})",
                    evidence=json.dumps(ent, sort_keys=True)[:200],
                )
            )
            continue
        ts = _parse_iso(str(ent.get("ts") or ""))
        if ts is not None and (now_f - ts) >= float(busy_max_s):
            age_m = int((now_f - ts) / 60)
            r0 = rows[0]
            rk = row_key(str(r0.get("task") or ""), str(r0.get("repo") or ""), str(r0.get("id") or ""))
            findings.append(
                Finding(
                    kind="stale_busy",
                    severity="warn",
                    nick=str(nick),
                    row_key=rk,
                    detail=f"busy for {age_m}m without DONE (threshold={int(busy_max_s)}s)",
                    evidence=str(ent.get("ts") or ""),
                )
            )

    # bored_skip busy while eligible work + no accepted for nick — needs log; queue-only hint:
    for nick, ent in workers.items():
        if not isinstance(ent, dict):
            continue
        if str(ent.get("state") or "").lower() != "busy":
            continue
        if accepted_by_nick.get(str(nick)):
            continue
        if unaccepted:
            findings.append(
                Finding(
                    kind="busy_blocks_eligible",
                    severity="error",
                    nick=str(nick),
                    detail=(
                        f"ghost busy while unaccepted={len(unaccepted)} "
                        "(bored_skip reason=busy will miss work)"
                    ),
                )
            )
    return findings


def scan_offers_dual(digest_home: Path, *, offer_window_s: float = 90.0) -> list[Finding]:
    """Detect same row_key open to two nicks (should be impossible after PR #196)."""
    doc = load_json(digest_home / "offers.json")
    open_map = doc.get("open") if isinstance(doc.get("open"), dict) else {}
    by_key: dict[str, list[str]] = defaultdict(list)
    now = time.time()
    for nick, ent in open_map.items():
        if not isinstance(ent, dict):
            continue
        row = ent.get("row") if isinstance(ent.get("row"), dict) else None
        if not isinstance(row, dict):
            continue
        offered_at = float(ent.get("offered_at") or 0.0)
        if offered_at > 0 and (now - offered_at) > float(offer_window_s) * 2:
            continue
        rk = row_key(str(row.get("task") or ""), str(row.get("repo") or ""), str(row.get("id") or ""))
        by_key[rk].append(str(nick))
    findings: list[Finding] = []
    for rk, nicks in by_key.items():
        if len(nicks) > 1:
            findings.append(
                Finding(
                    kind="dual_offer",
                    severity="error",
                    row_key=rk,
                    detail=f"open offer row_key held by {','.join(sorted(nicks))}",
                )
            )
    return findings


def scan_service_log(
    log_path: Path,
    *,
    ack_done_timeout_s: float,
    ack_no_match_storm: int,
    now: float | None = None,
    lookback_s: float = 2 * 3600,
) -> list[Finding]:
    now_f = time.time() if now is None else float(now)
    lines = _tail_lines(log_path)
    findings: list[Finding] = []
    cutoff = now_f - float(lookback_s)

    # Track last ACK per nick+job awaiting DONE
    open_acks: dict[tuple[str, str], float] = {}
    assigns: dict[str, list[tuple[float, str]]] = defaultdict(list)
    no_match_times: list[float] = []
    bored_busy: list[tuple[float, str]] = []

    for ln in lines:
        mts = _TS_RE.match(ln)
        ts = _parse_iso(mts.group(1)) if mts else None
        if ts is None:
            continue

        ma = _ASSIGN_RE.search(ln)
        if ma:
            if ts >= cutoff:
                rk = row_key(ma.group(2), ma.group(3), ma.group(4))
                assigns[rk].append((ts, ma.group(1)))
            continue

        mack = _ACK_RE.search(ln)
        if mack:
            nick, repo, num, mode = mack.group(1), mack.group(2), mack.group(3), mack.group(4)
            # Always track ACK/DONE pairs even before cutoff so a DONE inside
            # lookback can clear an older ACK; only alert if ACK still open.
            open_acks[(nick, row_key(mode, repo, num))] = ts
            continue

        md = _DONE_RE.search(ln)
        if md:
            nick, repo, num, mode = md.group(1), md.group(2), md.group(3), md.group(4)
            open_acks.pop((nick, row_key(mode, repo, num)), None)
            # Also clear any mode-mismatch key for same repo#n
            for key in list(open_acks):
                if key[0] == nick and key[1].startswith(f"{repo}|") and key[1].endswith(f"|#{num}"):
                    open_acks.pop(key, None)
            continue

        if _ACK_NO_MATCH_RE.search(ln):
            no_match_times.append(ts)
            continue

        mb = _BORED_BUSY_RE.search(ln)
        if mb:
            bored_busy.append((ts, mb.group(1)))

    for (nick, rk), ack_ts in open_acks.items():
        if ack_ts < cutoff:
            continue
        age = now_f - ack_ts
        if age >= float(ack_done_timeout_s):
            findings.append(
                Finding(
                    kind="ack_without_done",
                    severity="warn",
                    nick=nick,
                    row_key=rk,
                    detail=f"ACK age={int(age)}s without event=done (threshold={int(ack_done_timeout_s)}s)",
                )
            )

    # Dual assign: two different nicks for same row_key within offer window
    for rk, events in assigns.items():
        events = sorted(events)
        for i in range(len(events) - 1):
            t0, n0 = events[i]
            t1, n1 = events[i + 1]
            if n0 != n1 and (t1 - t0) <= 300:
                findings.append(
                    Finding(
                        kind="dual_assign_log",
                        severity="error",
                        row_key=rk,
                        detail=f"assign {n0} then {n1} within {int(t1 - t0)}s",
                        evidence=f"{n0}@{int(t0)} -> {n1}@{int(t1)}",
                    )
                )

    # ack_no_match storm: many in last 10 minutes
    recent_nm = [t for t in no_match_times if (now_f - t) <= 600]
    if len(recent_nm) >= int(ack_no_match_storm):
        findings.append(
            Finding(
                kind="ack_no_match_storm",
                severity="warn",
                detail=f"{len(recent_nm)} ack_no_match in last 10m (threshold={ack_no_match_storm})",
            )
        )

    # Recent bored_skip busy (last 15m) — surface for operators
    for ts, nick in bored_busy:
        if (now_f - ts) <= 900:
            findings.append(
                Finding(
                    kind="bored_skip_busy",
                    severity="info",
                    nick=nick,
                    detail="recent bored_skip reason=busy in service log",
                    evidence=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )

    return findings


def scan_outbox_vs_chair(
    outbox_paths: list[Path],
    log_path: Path,
    *,
    lag_s: float = 30.0,
    now: float | None = None,
) -> list[Finding]:
    """DONE in seat outbox not seen as event=done in chair log within lag_s."""
    now_f = time.time() if now is None else float(now)
    log_lines = _tail_lines(log_path)
    done_in_log: set[tuple[str, str, str]] = set()
    for ln in log_lines:
        md = _DONE_RE.search(ln)
        if md:
            done_in_log.add((md.group(4).upper(), md.group(2), md.group(3)))

    findings: list[Finding] = []
    for ob in outbox_paths:
        if not ob.is_file():
            continue
        try:
            mtime = ob.stat().st_mtime
            rows = ob.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        # Only alert when outbox recently touched (DONE likely pending/recent)
        if (now_f - mtime) > max(float(lag_s) * 20, 600):
            continue
        for ln in reversed(rows):
            m = _DONE_OUTBOX_RE.match(ln.strip())
            if not m:
                continue
            task, repo, num = m.group(1).upper(), m.group(2), m.group(3)
            if (task, repo, num) in done_in_log:
                break
            # Latest DONE in this outbox missing from chair log
            age = now_f - mtime
            if age >= float(lag_s):
                findings.append(
                    Finding(
                        kind="outbox_done_lag",
                        severity="error",
                        row_key=row_key(task, repo, num),
                        detail=(
                            f"DONE {task} {repo}#{num} in {ob} "
                            f"not in chair log (outbox mtime age={int(age)}s)"
                        ),
                        evidence=ln.strip()[:160],
                    )
                )
            break
    return findings


def discover_watch_outboxes() -> list[Path]:
    home = Path.home()
    found: list[Path] = []
    for p in home.glob(".agentic-irc-watch*/outbox.txt"):
        found.append(p)
    return found


def run_scan(
    *,
    digest_home: Path,
    jeeves_home: Path,
    log_path: Path,
    busy_max_s: float,
    ack_done_timeout_s: float,
    ack_no_match_storm: int,
    outbox_lag_s: float,
    include_outboxes: bool,
) -> ScanResult:
    q = load_json(digest_home / "queue.json")
    accepted_n = len([r for r in (q.get("accepted") or []) if isinstance(r, dict)])
    unacc_n = len([r for r in (q.get("unaccepted") or []) if isinstance(r, dict)])
    workers = q.get("workers") if isinstance(q.get("workers"), dict) else {}
    busy_n = sum(
        1
        for e in workers.values()
        if isinstance(e, dict) and str(e.get("state") or "").lower() == "busy"
    )
    findings: list[Finding] = []
    findings.extend(scan_queue_state(digest_home, busy_max_s=busy_max_s))
    findings.extend(scan_offers_dual(digest_home))
    findings.extend(
        scan_service_log(
            log_path,
            ack_done_timeout_s=ack_done_timeout_s,
            ack_no_match_storm=ack_no_match_storm,
        )
    )
    if include_outboxes:
        findings.extend(
            scan_outbox_vs_chair(
                discover_watch_outboxes(),
                log_path,
                lag_s=outbox_lag_s,
            )
        )
    # Dedup identical kind+nick+row_key+detail
    seen: set[str] = set()
    uniq: list[Finding] = []
    for f in findings:
        key = f"{f.kind}|{f.nick}|{f.row_key}|{f.detail}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return ScanResult(
        ts=_utc_now(),
        findings=uniq,
        workers_busy=busy_n,
        accepted=accepted_n,
        unaccepted=unacc_n,
    )


def emit_alerts(result: ScanResult, alert_path: Path, *, also_stdout: bool) -> None:
    alert_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not result.findings:
        lines.append(
            f"{result.ts} INFO handoff_ok busy={result.workers_busy} "
            f"accepted={result.accepted} unaccepted={result.unaccepted}"
        )
    for f in result.findings:
        lines.append(
            f"{result.ts} {f.severity.upper()} kind={f.kind} nick={f.nick or '-'} "
            f"row_key={f.row_key or '-'} detail={f.detail}"
            + (f" evidence={f.evidence}" if f.evidence else "")
        )
    text = "\n".join(lines) + "\n"
    with alert_path.open("a", encoding="utf-8") as fh:
        fh.write(text)
    if also_stdout:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Watch Jeeves handoff failures (local alerts only)")
    p.add_argument("--digest-home", type=Path, default=None)
    p.add_argument("--jeeves-home", type=Path, default=None)
    p.add_argument("--log", type=Path, default=None, help="bobjeeves-service.log path")
    p.add_argument("--alert-log", type=Path, default=None)
    p.add_argument("--busy-max-s", type=float, default=45 * 60)
    p.add_argument("--ack-done-timeout-s", type=float, default=20 * 60)
    p.add_argument("--ack-no-match-storm", type=int, default=5)
    p.add_argument("--outbox-lag-s", type=float, default=30)
    p.add_argument("--no-outbox", action="store_true", help="Skip seat outbox vs chair checks")
    p.add_argument("--once", action="store_true", help="Single scan (default)")
    p.add_argument("--loop", type=float, default=0, help="Repeat every N seconds")
    p.add_argument("--json", action="store_true", help="Print JSON scan result to stdout")
    p.add_argument("--quiet-ok", action="store_true", help="Do not append handoff_ok lines")
    args = p.parse_args(argv)

    digest_home = args.digest_home or default_digest_home()
    jeeves_home = args.jeeves_home or default_jeeves_home()
    log_path = args.log or default_service_log(jeeves_home)
    alert_path = args.alert_log or (jeeves_home / "handoff-alerts.log")

    interval = float(args.loop or 0)
    once = bool(args.once) or interval <= 0

    def one() -> ScanResult:
        return run_scan(
            digest_home=digest_home,
            jeeves_home=jeeves_home,
            log_path=log_path,
            busy_max_s=float(args.busy_max_s),
            ack_done_timeout_s=float(args.ack_done_timeout_s),
            ack_no_match_storm=int(args.ack_no_match_storm),
            outbox_lag_s=float(args.outbox_lag_s),
            include_outboxes=not args.no_outbox,
        )

    while True:
        result = one()
        if args.json:
            payload = {
                "ts": result.ts,
                "workers_busy": result.workers_busy,
                "accepted": result.accepted,
                "unaccepted": result.unaccepted,
                "findings": [asdict(f) for f in result.findings],
            }
            print(json.dumps(payload, indent=2))
        if result.findings or not args.quiet_ok:
            emit_alerts(result, alert_path, also_stdout=not args.json)
        if once:
            return 1 if any(f.severity == "error" for f in result.findings) else 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
