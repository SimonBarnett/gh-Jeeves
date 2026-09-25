"""FR #17: skill jeeves-install-service — complete SKILL + dry-run commands.

Never Apply / never live install. Never touch Ergo/BobIrcd.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jeeves import install_service as inst

ROOT = Path(__file__).resolve().parents[1]


def test_skill_file_complete_not_stub():
    missing = inst.skill_complete(ROOT / "skills" / "jeeves-install-service" / "SKILL.md")
    assert missing == [], f"skill incomplete: {missing}"


def test_installer_and_start_scripts_exist():
    assert (ROOT / "tools" / "Install-BobJeeves.ps1").is_file()
    assert (ROOT / "tools" / "Start-BobJeeves.ps1").is_file()


def test_installer_source_never_mutates_ircd():
    text = (ROOT / "tools" / "Install-BobJeeves.ps1").read_text(encoding="utf-8")
    assert not re.search(r"(?i)&\s*sc\.exe\s+(create|config|start|stop|delete)\s+BobIrcd", text)
    assert "Install-BobIrcd.ps1" not in text or "forbidden" in text.lower()
    # must document never touch
    assert "NeverTouchIrcd" in text or "never_touch_ircd" in text or "NEVER" in text
    assert re.search(r"(?i)-DryRun", text)


def test_dry_run_install_plan_json():
    plan = inst.run_dry_run(ROOT)
    assert plan.get("_exit_code") == 0
    assert plan.get("dry_run") is True
    assert plan.get("apply") is False
    assert plan.get("service_name") == "BobJeeves"
    assert plan.get("never_touch_ircd") is True
    assert plan.get("homes_distinct") is True
    assert plan.get("ok") is True
    forbidden = plan.get("forbidden") or []
    assert any("BobIrcd" in str(x) or "ircd" in str(x).lower() for x in forbidden)
    steps = plan.get("steps") or []
    assert steps, "plan must list steps"
    # must not claim it started a live service in dry-run
    joined = " ".join(str(s) for s in steps).lower()
    assert "apply completed" not in joined


def test_dry_run_start_helper():
    data = inst.run_start_dry_run(ROOT)
    assert data.get("_exit_code") == 0
    assert data.get("dry_run") is True
    assert "Ergo" in (data.get("never_touch") or [])


def test_identical_homes_fail_plan(tmp_path: Path):
    """Homes must differ — invoke with same path via -JeevesHome/-DigestHome."""
    import json
    import subprocess

    same = tmp_path / "same-home"
    same.mkdir()
    script = ROOT / "tools" / "Install-BobJeeves.ps1"
    proc = subprocess.run(
        [
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
            "-JeevesHome",
            str(same),
            "-DigestHome",
            str(same),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    i = out.find("{")
    assert i >= 0, f"out={out!r} err={err!r} code={proc.returncode}"
    plan = json.loads(out[i:])
    assert plan.get("ok") is False
    assert plan.get("homes_distinct") is False
    assert proc.returncode == 2


def test_skill_readme_lists_install_service():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-install-service" in readme
