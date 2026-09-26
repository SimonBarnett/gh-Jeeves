"""FR #22: skill jeeves-release — complete SKILL + dry-run commands.

Never Apply from FR workers. Never touch Ergo/BobIrcd.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jeeves import release_tool

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-release" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = release_tool.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required():
    text = SKILL.read_text(encoding="utf-8")
    assert "Commands" in text
    assert "Overlay" in text or "overlay" in text.lower()
    assert "Deploy-BobJeevesRelease.ps1" in text
    assert "python -m jeeves.release_tool" in text
    assert "--dry-run" in text or "-DryRun" in text
    assert "JEEVES_RELEASE_TAG" in text
    assert "drift" in text.lower()
    assert "G2" in text or "g2" in text.lower()
    assert "rollback" in text.lower() or "Roll back" in text


def test_skill_readme_lists_release():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-release" in readme
    assert "#22" in readme or "22" in readme


def test_deploy_script_exists_and_defaults_dry_run():
    script = ROOT / "tools" / "Deploy-BobJeevesRelease.ps1"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "DryRun" in text
    assert "never" in text.lower() or "Never" in text
    assert "BobIrcd" in text or "Ergo" in text


def test_dry_run_release_json():
    plan = release_tool.run_dry_run(ROOT)
    assert plan.get("dry_run") is True
    assert plan.get("apply") is False or plan.get("mutated") is False
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    assert plan.get("never_touch_ircd") is True
    assert "version" in plan or "release_tag" in plan


def test_cli_dry_run_invokable():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves.release_tool", "--dry-run", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc.get("dry_run") is True


def test_cli_via_jeeves_release_mode():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves", "release", "--dry-run", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc.get("dry_run") is True
