"""FR #48: installer full IRC cmdline from config; no BobIrcd dependency."""

from __future__ import annotations

import json
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
