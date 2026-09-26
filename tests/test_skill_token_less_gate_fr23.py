"""FR #23: skill jeeves-token-less-gate — complete SKILL + dry-run commands.

The gate itself is script-only; this skill is an overlay for operators.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jeeves import token_less_gate_tool

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-token-less-gate" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = token_less_gate_tool.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required():
    text = SKILL.read_text(encoding="utf-8")
    assert "Commands" in text
    assert "Overlay" in text or "overlay" in text.lower()
    assert "python -m jeeves.token_less_gate_tool" in text
    assert "--dry-run" in text
    assert "G1" in text and "G2" in text
    assert "g1_token_less_e2e" in text
    assert "no LLM" in text.lower() or "no-LLM" in text or "token-less" in text.lower()
    # FR #106: Jeeves assigns (not ear OFFER)
    assert "Jeeves assigns" in text or "assigns" in text.lower()


def test_skill_readme_lists_gate():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-token-less-gate" in readme
    assert "#23" in readme or "23" in readme


def test_g1_and_g2_artifacts_exist():
    assert (ROOT / "tests" / "g1_token_less_e2e").is_dir()
    assert (ROOT / "docs" / "g2-live-smoke-checklist.md").is_file()
    assert (ROOT / "src" / "jeeves" / "guard.py").is_file()


def test_dry_run_collects_g1_without_live_irc():
    plan = token_less_gate_tool.run_dry_run(ROOT)
    assert plan.get("dry_run") is True
    assert plan.get("mutated") is False
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    assert plan.get("g1_collect_ok") is True
    assert int(plan.get("g1_test_count") or 0) >= 1
    assert plan.get("g2_checklist_present") is True
    assert plan.get("never_requires_llm") is True


def test_cli_dry_run_invokable():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves.token_less_gate_tool", "--dry-run", "--json"],
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


def test_cli_via_jeeves_gate_mode():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves", "gate", "--dry-run", "--json"],
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
