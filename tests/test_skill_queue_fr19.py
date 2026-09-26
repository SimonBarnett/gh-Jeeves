"""FR #19: skill jeeves-queue — complete SKILL + dry-run commands.

Dry-run first for any manual edit. Overlay: token-less path does not depend
on this skill.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jeeves import queue_tool

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-queue" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = queue_tool.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required_ops():
    text = SKILL.read_text(encoding="utf-8")
    assert "supersede" in text.lower()
    assert "!list" in text
    assert "resync" in text.lower()
    assert "dry-run" in text.lower() or "DryRun" in text or "--dry-run" in text
    assert "Commands" in text
    assert "Overlay" in text or "overlay" in text.lower()
    assert "python -m jeeves.queue_tool" in text
    assert "queue.json" in text
    assert "ignored.json" in text or "!ignore" in text
    assert "focus" in text.lower()


def test_skill_readme_lists_queue():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-queue" in readme
    assert "#19" in readme or "19" in readme


def test_dry_run_queue_json_offline(tmp_path: Path):
    home = tmp_path / "digest"
    home.mkdir()
    from jeeves.queue import Claim, apply_queue_event, save_queue, empty_queue

    save_queue(home, empty_queue())
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "FR", "#19", "issues", "opened", "skill"),
    )
    plan = queue_tool.run_dry_run(ROOT, digest_home=home)
    assert plan.get("dry_run") is True
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    assert plan.get("mutated") is False
    assert plan.get("counts", {}).get("unaccepted") >= 1
    assert any("FR" in str(r.get("task")) for r in (plan.get("unaccepted_sample") or []))
    rules = plan.get("rules") or {}
    assert isinstance(rules, dict) and len(rules) >= 4
    assert any("MRB" in str(v) or "FR" in str(v) for v in rules.values())


def test_cli_dry_run_invokable(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    from jeeves.queue import empty_queue, save_queue

    save_queue(home, empty_queue())
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "BOB_DIGEST_HOME": str(home),
    }
    proc = subprocess.run(
        ["python", "-m", "jeeves.queue_tool", "--dry-run", "--json"],
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


def test_cli_via_jeeves_queue_mode(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    from jeeves.queue import empty_queue, save_queue

    save_queue(home, empty_queue())
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "BOB_DIGEST_HOME": str(home),
    }
    proc = subprocess.run(
        ["python", "-m", "jeeves", "queue", "--dry-run", "--json"],
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
