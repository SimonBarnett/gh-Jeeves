"""FR #48: installer full IRC cmdline from config; no BobIrcd dependency."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_install(*extra: str) -> dict:
    script = ROOT / "tools" / "Install-BobJeeves.ps1"
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-DryRun",
        "-Json",
        "-RepoRoot",
        str(ROOT),
        *extra,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    i = out.find("{")
    assert i >= 0, f"code={proc.returncode} err={err!r} out={out[:500]!r}"
    plan = json.loads(out[i:])
    plan["_exit_code"] = proc.returncode
    return plan


def _run_start(*extra: str) -> dict:
    script = ROOT / "tools" / "Start-BobJeeves.ps1"
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-DryRun",
        "-RepoRoot",
        str(ROOT),
        *extra,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    i = out.find("{")
    assert i >= 0, out
    plan = json.loads(out[i:])
    plan["_exit_code"] = proc.returncode
    return plan


def test_example_config_exists():
    assert (ROOT / "config" / "bobjeeves.example.json").is_file()
    cfg = json.loads((ROOT / "config" / "bobjeeves.example.json").read_text(encoding="utf-8"))
    assert cfg.get("topology") == "combined"
    assert int(cfg.get("receiver_port") or 0) == 19781
    assert cfg.get("tls") is True
    assert "BobIrcd" in (cfg.get("never_depend_on") or [])
    # FR #72: ionos-shaped auth + receiver secret + resync toggle keys
    assert "password_file" in cfg
    assert "receiver_secret_file" in cfg
    assert "disable_resync" in cfg
    assert cfg.get("sasl_user") in ("", None)


def test_production_dryrun_cmdline_has_host_port_tls():
    plan = _run_install("-Production")
    assert plan.get("_exit_code") == 0
    assert plan.get("ok") is True
    assert plan.get("tls") is True
    assert plan.get("irc_host")
    assert int(plan.get("irc_port") or 0) == 6697
    assert int(plan.get("receiver_port") or 0) == 19781
    assert plan.get("no_bobircd_dependency") is True
    assert plan.get("depends_on") in (None, "", [])
    cmd = str(plan.get("service_cmdline") or "")
    assert "--host" in cmd
    assert "--port" in cmd
    assert "6697" in cmd
    assert "--tls" in cmd
    assert "19781" in cmd
    assert "-m jeeves" in cmd or "jeeves all" in cmd
    # must not depend on BobIrcd
    steps = " ".join(str(s) for s in (plan.get("steps") or []))
    assert "depend= BobIrcd" not in steps
    assert "NO depend" in steps or "no depend" in steps.lower() or "no_bobircd" in str(plan).lower()


def test_installer_source_forbids_bobircd_depend():
    text = (ROOT / "tools" / "Install-BobJeeves.ps1").read_text(encoding="utf-8")
    assert not re.search(r"(?i)sc\.exe\s+(create|config)[^\n]*\bdepend=\s*BobIrcd", text)
    assert "no_bobircd_dependency" in text or "NO depend" in text
    assert "19781" in text
    assert "bobjeeves" in text.lower()


def test_start_helper_production_cmdline():
    plan = _run_start("-Production")
    assert plan.get("_exit_code") == 0
    assert plan.get("tls") is True
    cmd = str(plan.get("service_cmdline") or "")
    assert "--tls" in cmd
    assert "6697" in cmd or str(plan.get("irc_port")) == "6697"
    assert str(plan.get("receiver_port")) == "19781"
    assert plan.get("topology") == "combined"
    assert plan.get("no_bobircd_dependency") is True


def test_skill_documents_combined_topology():
    skill = (ROOT / "skills" / "jeeves-install-service" / "SKILL.md").read_text(encoding="utf-8")
    assert "19781" in skill or "combined" in skill.lower() or "bobjeeves" in skill.lower()


def test_hydrate_sasl_password_file(tmp_path: Path, monkeypatch):
    """nssm sets *_PASSWORD_FILE; jeeves must load into AGENTIC_IRC_SASL_PASSWORD."""
    from jeeves.env_secrets import hydrate_secrets_from_files

    secret = tmp_path / "sasl.pass"
    secret.write_text("s3cret-line\n", encoding="utf-8")
    monkeypatch.delenv("AGENTIC_IRC_SASL_PASSWORD", raising=False)
    monkeypatch.setenv("AGENTIC_IRC_SASL_PASSWORD_FILE", str(secret))
    hydrate_secrets_from_files()
    assert os.environ.get("AGENTIC_IRC_SASL_PASSWORD") == "s3cret-line"


def test_hydrate_server_password_file(tmp_path: Path, monkeypatch):
    """FR #72: non-SASL Ergo server password via AGENTIC_IRC_PASSWORD_FILE."""
    from jeeves.env_secrets import hydrate_secrets_from_files

    secret = tmp_path / "server.pass"
    secret.write_text("ergo-server-pw\n", encoding="utf-8")
    monkeypatch.delenv("AGENTIC_IRC_PASSWORD", raising=False)
    monkeypatch.delenv("AGENTIC_IRC_SASL_PASSWORD", raising=False)
    monkeypatch.setenv("AGENTIC_IRC_PASSWORD_FILE", str(secret))
    hydrate_secrets_from_files()
    assert os.environ.get("AGENTIC_IRC_PASSWORD") == "ergo-server-pw"


def test_install_dryrun_receiver_secret_and_password_and_no_resync(tmp_path: Path):
    """FR #72: DryRun surfaces receiver_secret_file, password auth, disable_resync."""
    cfg = {
        "topology": "combined",
        "nick": "Jeeves",
        "irc_host": "irc.ntsa.uk",
        "irc_port": 6697,
        "tls": True,
        "sasl_user": "",
        "sasl_password_file": "",
        "password_file": str(tmp_path / "pw.txt"),
        "receiver_secret_file": str(tmp_path / "bob.secret"),
        "receiver_port": 19781,
        "jeeves_home": str(tmp_path / "jeeves"),
        "digest_home": str(tmp_path / "digest"),
        "disable_resync": True,
        "never_depend_on": ["BobIrcd"],
    }
    (tmp_path / "pw.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "bob.secret").write_text("sec\n", encoding="utf-8")
    cfg_path = tmp_path / "bobjeeves.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    plan = _run_install("-ConfigPath", str(cfg_path), "-Production")
    assert plan.get("_exit_code") == 0, plan
    assert plan.get("ok") is True
    assert plan.get("disable_resync") is True
    assert plan.get("auth_mode") == "server_password"
    assert str(plan.get("password_file") or "").endswith("pw.txt")
    assert str(plan.get("receiver_secret_file") or "").endswith("bob.secret")
    args = " ".join(str(a) for a in (plan.get("python_args") or []))
    assert "--no-resync" in args
    # source must set BOB_CALLBACK_SECRET_FILE on Apply path
    text = (ROOT / "tools" / "Install-BobJeeves.ps1").read_text(encoding="utf-8")
    assert "BOB_CALLBACK_SECRET_FILE" in text
    assert "JEEVES_RESYNC_DISABLE" in text


def test_g1_tls_fixture_exists():
    """FR #72: pre-generated certs so Windows without openssl still runs G1 TLS."""
    d = ROOT / "tests" / "fixtures" / "g1_tls"
    assert (d / "g1.pem").is_file()
    assert (d / "g1.key").is_file()


def test_receiver_default_is_19781():
    """FR #48: bare `python -m jeeves` matches IIS/helper default (not 8765)."""
    from jeeves.__main__ import _parse

    old = os.environ.pop("BOB_REPORT_PORT", None)
    try:
        args = _parse(["all"])
        assert args.receiver_port == 19781
    finally:
        if old is not None:
            os.environ["BOB_REPORT_PORT"] = old
