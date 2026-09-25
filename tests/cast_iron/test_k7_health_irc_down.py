"""K7 / FR #8: report-only IRC-down health; never touch Ergo/BobIrcd.

Seed outcome: Jeeves health detects IRC server down; gh-Jeeves must not remediate
BobIrcd/Ergo (raise agentic_build #327 for Simon).
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from jeeves import health


class _FakeSock:
    def close(self) -> None:
        return None


def test_k7_fail_when_irc_unreachable():
    """Gate assert: unreachable IRC ⇒ report ok=False, irc_reachable=False."""

    def boom(addr, timeout=0):
        raise ConnectionRefusedError(111, "refused")

    rep = health.run_health(
        irc_host="127.0.0.1",
        irc_port=1,
        tls=False,
        open_socket=boom,
        service_probe=None,
        check_bobjeeves=False,
        check_bobircd=False,
    )
    assert rep.irc_reachable is False, "K7 FAIL: must detect IRC down"
    assert rep.ok is False
    assert rep.irc_detail
    assert any("327" in r or "agentic_build" in r for r in rep.raise_for)
    assert "Ergo" in rep.summary_line() or "irc_down" in rep.summary_line() or "DEGRADED" in rep.summary_line()


def test_k7_ok_when_tcp_accepts():
    def ok(addr, timeout=0):
        return _FakeSock()

    rep = health.run_health(
        irc_host="irc.example",
        irc_port=6697,
        tls=False,
        open_socket=ok,
        check_bobircd=False,
        check_bobjeeves=False,
    )
    assert rep.irc_reachable is True
    assert rep.ok is True


def test_k7_bobircd_stopped_is_reported_not_remediated():
    def ok(addr, timeout=0):
        return _FakeSock()

    def svc(name: str) -> str:
        if name == "BobIrcd":
            return "stopped"
        if name == "BobJeeves":
            return "running"
        return "unknown"

    rep = health.run_health(
        irc_host="127.0.0.1",
        irc_port=6697,
        tls=False,
        open_socket=ok,
        service_probe=svc,
    )
    assert rep.irc_reachable is True
    assert rep.bobircd_service == "stopped"
    assert rep.bobjeeves_service == "running"
    assert any("Stopped" in n or "stopped" in n.lower() for n in rep.notes)
    assert any("327" in r for r in rep.raise_for)
    # report-only: health module has no remediation hooks in return value
    assert not hasattr(rep, "actions")
    d = rep.to_dict()
    assert "remediate" not in d and "restart" not in str(d).lower()


def test_k7_source_never_mutates_bobircd_or_ircd_yaml():
    src = Path(health.__file__).read_text(encoding="utf-8")
    hits = health.health_source_forbids_ircd_mutation(src)
    assert hits == [], f"health.py must not contain Ergo/BobIrcd mutation: {hits}"
    # positive: sc.exe query is ok if present; create/start forbidden
    assert "sc.exe query" in src or "query" in src  # read-only path allowed
    for banned in ("Install-BobIrcd", "Start-Service", "ircd.yaml"):
        # may appear only inside FORBIDDEN pattern strings or comments about never touching
        if banned == "ircd.yaml":
            # allowed in FORBIDDEN_HEALTH_ACTIONS and docs strings about not editing
            continue
        if banned in src and f'"{banned}"' not in src and f"'{banned}'" not in src:
            # Start-Service only inside regex forbidden list
            if banned == "Start-Service" and "Start-Service" in "".join(
                health.FORBIDDEN_HEALTH_ACTIONS
            ):
                continue


def test_k7_timeout_is_down():
    def hang(addr, timeout=0):
        raise socket.timeout("timed out")

    rep = health.run_health(
        irc_host="10.255.255.1",
        irc_port=6697,
        tls=False,
        open_socket=hang,
        check_bobircd=False,
        check_bobjeeves=False,
    )
    assert rep.ok is False
    assert rep.irc_reachable is False
    assert "timeout" in rep.irc_detail.lower() or rep.probes


def test_k7_write_report_json(tmp_path: Path):
    def boom(addr, timeout=0):
        raise OSError("down")

    rep = health.run_health(
        open_socket=boom,
        tls=False,
        check_bobircd=False,
        check_bobjeeves=False,
    )
    out = tmp_path / "health.json"
    health.write_health_report(out, rep)
    text = out.read_text(encoding="utf-8")
    assert '"irc_reachable": false' in text.replace("False", "false")
    assert "raise_for" in text


def test_k7_cli_json(monkeypatch, capsys):
    def fake_probe_tcp(*_a, **_k):
        return health.ProbeResult("irc", False, "down", "irc_down")

    monkeypatch.setattr(health, "probe_tcp", fake_probe_tcp)
    rc = health.main(["--json", "--no-service-probe", "--no-tls"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "irc_reachable" in out
