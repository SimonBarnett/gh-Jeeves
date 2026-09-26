"""FR #20: skill jeeves-announce-debug — complete SKILL + dry-run commands.

Overlay: token-less path does not depend on this skill.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jeeves import announce_tool

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-announce-debug" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = announce_tool.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required_path():
    text = SKILL.read_text(encoding="utf-8")
    assert "Commands" in text
    assert "Overlay" in text or "overlay" in text.lower()
    assert "python -m jeeves.announce_tool" in text
    assert "--dry-run" in text
    assert "/bob/v1/git" in text or "webhook" in text.lower()
    assert "417" in text or "length" in text.lower()
    assert "secret" in text.lower()
    assert "chair-outbox" in text.lower() or "outbox" in text.lower()
    assert "#bobiverse" in text


def test_skill_readme_lists_announce_debug():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-announce-debug" in readme
    assert "#20" in readme or "20" in readme


def test_dry_run_formats_sample_and_secret_filter():
    plan = announce_tool.run_dry_run(ROOT)
    assert plan.get("dry_run") is True
    assert plan.get("mutated") is False
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    assert plan.get("sample_announce")
    assert plan.get("sample_announce", "").startswith("GIT")
    # title mentioning ghp_ must not reject (field-only filter)
    assert plan.get("title_mentions_secret_rejected") is False
    # real secret field must reject
    assert plan.get("secret_field_rejected") is True
    assert plan.get("length_ok") is True


def test_cli_dry_run_invokable():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves.announce_tool", "--dry-run", "--json"],
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


def test_cli_via_jeeves_announce_mode():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves", "announce", "--dry-run", "--json"],
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
