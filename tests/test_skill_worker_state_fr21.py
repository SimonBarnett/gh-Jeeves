"""FR #21: skill jeeves-worker-state — complete SKILL + dry-run commands.

Overlay: token-less path does not depend on this skill.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jeeves import worker_state_tool

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-worker-state" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = worker_state_tool.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required():
    text = SKILL.read_text(encoding="utf-8")
    assert "Commands" in text
    assert "Overlay" in text or "overlay" in text.lower()
    assert "python -m jeeves.worker_state_tool" in text
    assert "--dry-run" in text
    assert "ACK" in text and "DONE" in text
    assert "working_on" in text or "TipForm" in text
    assert "busy" in text.lower() and "idle" in text.lower()
    assert "hidden" in text.lower() or "console" in text.lower() or "K12" in text


def test_skill_readme_lists_worker_state():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-worker-state" in readme
    assert "#21" in readme or "21" in readme


def test_dry_run_ack_done_tipform_path(tmp_path: Path):
    plan = worker_state_tool.run_dry_run(ROOT, digest_home=tmp_path / "d")
    assert plan.get("dry_run") is True
    assert plan.get("mutated") is False
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    assert plan.get("after_ack", {}).get("busy") is True
    assert plan.get("after_ack", {}).get("working_on")
    assert plan.get("after_done", {}).get("busy") is False
    assert plan.get("after_done", {}).get("working_on") in ("", None)


def test_cli_dry_run_invokable(tmp_path: Path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "BOB_DIGEST_HOME": str(tmp_path / "empty"),
    }
    (tmp_path / "empty").mkdir()
    proc = subprocess.run(
        ["python", "-m", "jeeves.worker_state_tool", "--dry-run", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc.get("dry_run") is True
    assert doc.get("mutated") is False


def test_cli_via_jeeves_worker_mode(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "BOB_DIGEST_HOME": str(home),
    }
    proc = subprocess.run(
        ["python", "-m", "jeeves", "worker-state", "--dry-run", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    doc = json.loads(proc.stdout)
    assert doc.get("dry_run") is True
