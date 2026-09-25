"""K7 / FR #8: Jeeves health — report-only IRC reachability.

Detects IRC server down (TCP/TLS probe). Reports Windows service state when
probes are injected. **Never** starts/stops/creates/deletes Ergo or BobIrcd,
and never edits ``ircd.yaml``. Raise Ergo/BobIrcd fixes in agentic_build (#327).
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# Hard ban: health code must never contain these action forms (tests scan source).
FORBIDDEN_HEALTH_ACTIONS = (
    r"(?i)sc\.exe\s+(create|config|start|stop|delete)\s+BobIrcd",
    r"(?i)Install-BobIrcd",
    r"(?i)Stop-Service\s+.*BobIrcd",
    r"(?i)Start-Service\s+.*BobIrcd",
    r"(?i)nssm\s+(install|start|stop|remove)\s+BobIrcd",
    r"(?i)open\(['\"].*ircd\.yaml",
    r"(?i)write.*ircd\.yaml",
)

DEFAULT_IRC_HOST = "127.0.0.1"
DEFAULT_IRC_PORT = 6697
DEFAULT_CONNECT_TIMEOUT_S = 3.0


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    code: str = ""  # machine-stable status code


@dataclass
class HealthReport:
    """Serializable health snapshot — report only, no remediation."""

    ts: str
    ok: bool
    irc_reachable: bool
    irc_detail: str = ""
    bobircd_service: str = "unknown"  # running|stopped|missing|unknown|n/a
    bobjeeves_service: str = "unknown"
    notes: list[str] = field(default_factory=list)
    raise_for: list[str] = field(default_factory=list)  # tickets / owners
    probes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary_line(self) -> str:
        if self.ok and self.irc_reachable:
            return f"Jeeves health: ok irc_up bobircd={self.bobircd_service} bobjeeves={self.bobjeeves_service}"
        parts = ["Jeeves health: DEGRADED"]
        if not self.irc_reachable:
            parts.append(f"irc_down ({self.irc_detail or 'unreachable'})")
        if self.bobircd_service == "stopped":
            parts.append("BobIrcd=Stopped (report-only; raise agentic_build#327)")
        if self.bobjeeves_service not in ("running", "n/a", "unknown"):
            parts.append(f"BobJeeves={self.bobjeeves_service}")
        return " ".join(parts)


ServiceProbe = Callable[[str], str]
"""Return service status: running|stopped|missing|unknown."""


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def probe_tcp(
    host: str,
    port: int,
    *,
    timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    tls: bool = False,
    open_socket: Callable[..., socket.socket] | None = None,
) -> ProbeResult:
    """
    Attempt TCP (optionally TLS) connect to IRC host:port.
    Pure connectivity — no IRC NICK/USER and no service manager calls.
    """
    host = (host or "").strip() or DEFAULT_IRC_HOST
    port = int(port)
    timeout_s = float(timeout_s)
    name = f"irc_tcp{'_tls' if tls else ''}"
    opener = open_socket or socket.create_connection
    try:
        raw = opener((host, port), timeout=timeout_s)
        try:
            if tls:
                ctx = ssl.create_default_context()
                # Fleet Ergo may use private CA; report handshake failure without mutating host.
                try:
                    with ctx.wrap_socket(raw, server_hostname=host) as _ss:
                        pass
                except ssl.SSLError as e:
                    # TCP up but TLS failed — still "server listening" for down-detection
                    return ProbeResult(
                        name=name,
                        ok=True,
                        detail=f"tcp_up tls_warn={type(e).__name__}",
                        code="irc_tcp_up_tls_warn",
                    )
            else:
                raw.close()
        finally:
            try:
                raw.close()
            except OSError:
                pass
        return ProbeResult(name=name, ok=True, detail=f"{host}:{port} open", code="irc_up")
    except (TimeoutError, socket.timeout) as e:
        return ProbeResult(
            name=name,
            ok=False,
            detail=f"{host}:{port} timeout {timeout_s}s",
            code="irc_down_timeout",
        )
    except OSError as e:
        return ProbeResult(
            name=name,
            ok=False,
            detail=f"{host}:{port} {type(e).__name__}:{getattr(e, 'errno', '')}",
            code="irc_down",
        )


def probe_service_status(name: str, probe: ServiceProbe | None) -> ProbeResult:
    """Report Windows service status via injected probe — never starts/stops."""
    if probe is None:
        return ProbeResult(
            name=f"service:{name}",
            ok=True,
            detail="probe not configured",
            code="n/a",
        )
    try:
        status = (probe(name) or "unknown").strip().lower()
    except Exception as e:
        return ProbeResult(
            name=f"service:{name}",
            ok=False,
            detail=f"probe_error {type(e).__name__}",
            code="unknown",
        )
    if status not in ("running", "stopped", "missing", "unknown", "n/a"):
        status = "unknown"
    # stopped BobIrcd is a known drift (K7) — report, do not treat as health module failure alone
    ok = status in ("running", "n/a", "unknown", "missing") or name != "BobIrcd"
    if name == "BobIrcd" and status == "stopped":
        ok = True  # report-only: detection succeeds even when service stopped
    return ProbeResult(
        name=f"service:{name}",
        ok=ok,
        detail=status,
        code=status,
    )


def run_health(
    *,
    irc_host: str = DEFAULT_IRC_HOST,
    irc_port: int = DEFAULT_IRC_PORT,
    tls: bool = True,
    timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    service_probe: ServiceProbe | None = None,
    open_socket: Callable[..., socket.socket] | None = None,
    check_bobjeeves: bool = True,
    check_bobircd: bool = True,
) -> HealthReport:
    """
    Build a report-only health snapshot.

    - IRC down ⇒ ``ok=False``, ``irc_reachable=False``, raise_for agentic_build.
    - BobIrcd Stopped while IRC up ⇒ note + raise_for #327 (K7 evidence mode).
    - Never mutates services or config.
    """
    notes: list[str] = []
    raise_for: list[str] = []
    probes: list[ProbeResult] = []

    irc = probe_tcp(
        irc_host,
        irc_port,
        timeout_s=timeout_s,
        tls=tls,
        open_socket=open_socket,
    )
    probes.append(irc)

    bobircd = "n/a"
    if check_bobircd:
        sp = probe_service_status("BobIrcd", service_probe)
        probes.append(sp)
        bobircd = sp.code if sp.code else sp.detail
        if sp.code == "stopped":
            notes.append(
                "BobIrcd service reports Stopped (Ergo may still run outside the service)"
            )
            raise_for.append("agentic_build#327")
            raise_for.append("Simon: Ergo/BobIrcd install — gh-Jeeves must not remediate")

    bobjeeves = "n/a"
    if check_bobjeeves:
        spj = probe_service_status("BobJeeves", service_probe)
        probes.append(spj)
        bobjeeves = spj.code if spj.code else spj.detail
        if spj.code == "stopped":
            notes.append("BobJeeves service Stopped")
            raise_for.append("gh-Jeeves: BobJeeves service (Install-BobJeeves / operator)")

    if not irc.ok:
        notes.append("IRC server unreachable — chair cannot join; do not restart Ergo from gh-Jeeves")
        raise_for.append("agentic_build#327")
        raise_for.append("Simon: IRC host down or BobIrcd/Ergo not listening")

    # overall ok: IRC must be reachable for Jeeves chair path
    overall = bool(irc.ok)
    # de-dupe raise_for
    seen: set[str] = set()
    raise_unique: list[str] = []
    for r in raise_for:
        if r not in seen:
            seen.add(r)
            raise_unique.append(r)

    return HealthReport(
        ts=_utc(),
        ok=overall,
        irc_reachable=bool(irc.ok),
        irc_detail=irc.detail,
        bobircd_service=bobircd,
        bobjeeves_service=bobjeeves,
        notes=notes,
        raise_for=raise_unique,
        probes=[asdict(p) for p in probes],
    )


def health_source_forbids_ircd_mutation(source: str) -> list[str]:
    """Return matched forbidden patterns (empty = clean).

    Strips the FORBIDDEN_HEALTH_ACTIONS definition so the patterns do not
    match themselves.
    """
    body = re.sub(
        r"FORBIDDEN_HEALTH_ACTIONS\s*=\s*\([\s\S]*?\)\n",
        "FORBIDDEN_HEALTH_ACTIONS = ()\n",
        source,
        count=1,
    )
    hits: list[str] = []
    for pat in FORBIDDEN_HEALTH_ACTIONS:
        if re.search(pat, body):
            hits.append(pat)
    return hits


def write_health_report(path: Path, report: HealthReport) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Jeeves health (report-only; never touches Ergo/BobIrcd)")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--tls", action="store_true", default=True)
    p.add_argument("--no-tls", action="store_true")
    p.add_argument("--timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_S)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", default="", help="write JSON report path")
    p.add_argument("--no-service-probe", action="store_true")
    args = p.parse_args(argv)

    import os

    host = args.host or os.environ.get("AGENTIC_IRC_HOST") or DEFAULT_IRC_HOST
    port = int(args.port or os.environ.get("AGENTIC_IRC_PORT") or DEFAULT_IRC_PORT)
    tls = not args.no_tls

    probe: ServiceProbe | None = None
    if not args.no_service_probe and os.name == "nt":
        probe = _windows_service_probe

    report = run_health(
        irc_host=host,
        irc_port=port,
        tls=tls,
        timeout_s=args.timeout,
        service_probe=probe,
    )
    if args.out:
        write_health_report(Path(args.out), report)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary_line())
        for n in report.notes:
            print(f"NOTE {n}")
        for r in report.raise_for:
            print(f"RAISE {r}")
    return 0 if report.ok else 1


def _windows_service_probe(name: str) -> str:
    """Read-only sc query. Never start/stop."""
    import subprocess

    try:
        proc = subprocess.run(
            ["sc.exe", "query", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    out = (proc.stdout or "") + (proc.stderr or "")
    if "FAILED" in out.upper() and "1060" in out:
        return "missing"
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", out, re.I)
    if not m:
        return "unknown"
    st = m.group(1).upper()
    if st == "RUNNING":
        return "running"
    if st in ("STOPPED", "STOP_PENDING"):
        return "stopped"
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
