"""FR #18: skill jeeves-health — complete SKILL + dry-run commands.

Report-only. Never touch Ergo/BobIrcd. Overlay: token-less path does not
depend on this skill.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from jeeves import health

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jeeves-health" / "SKILL.md"


def test_skill_file_complete_not_stub():
    missing = health.skill_complete(SKILL)
    assert missing == [], f"skill incomplete: {missing}"


def test_skill_documents_required_checks():
    text = SKILL.read_text(encoding="utf-8")
    assert "lastSeen" in text or "last seen" in text.lower()
    assert "version" in text.lower() and "drift" in text.lower()
    assert "throttle" in text.lower() or "backoff" in text.lower()
    assert "BobJeeves" in text
    assert "#bobiverse" in text
    assert "never" in text.lower() and ("Ergo" in text or "BobIrcd" in text)
    assert "Commands" in text or "dry-run" in text.lower() or "DryRun" in text
    assert "python -m jeeves health" in text or "jeeves health" in text


def test_skill_readme_lists_health():
    readme = (ROOT / "skills" / "README.md").read_text(encoding="utf-8")
    assert "jeeves-health" in readme
    assert "#18" in readme or "18" in readme


def test_dry_run_health_json_offline():
    """Documented dry-run must run without live IRC or service mutation."""
    plan = health.run_dry_run(ROOT)
    assert plan.get("dry_run") is True
    assert plan.get("report_only") is True
    assert plan.get("never_touch_ircd") is True
    assert plan.get("ok") is True
    assert plan.get("skill_complete") == []
    # offline: no socket / no Apply
    assert plan.get("irc_probed") is False
    assert "version" in plan
    assert "throttle" in plan or "backoff" in plan
    forbidden = plan.get("forbidden") or []
    assert any("BobIrcd" in str(x) or "Ergo" in str(x) for x in forbidden)


def test_cli_dry_run_invokable():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        ["python", "-m", "jeeves", "health", "--dry-run", "--json"],
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
    assert doc.get("never_touch_ircd") is True


def test_health_source_still_forbids_ircd_mutation():
    src = Path(health.__file__).read_text(encoding="utf-8")
    assert health.health_source_forbids_ircd_mutation(src) == []
