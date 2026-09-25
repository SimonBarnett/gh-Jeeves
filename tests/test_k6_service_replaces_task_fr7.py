"""FR #7 / K6: BobJeeves Windows service replaces scheduled task BobJeeves-chair.

Seed outcome: Jeeves runs as its own Automatic service (agentic_build #330),
not from task BobJeeves-chair. Failing-test-first encodes the dry-run contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeeves.install_service import INSTALL_PS1, run_dry_run

ROOT = Path(__file__).resolve().parents[1]


def test_k6_installer_exists():
    assert INSTALL_PS1.is_file()


def test_k6_dry_run_plan_auto_service_disables_task():
    """Plan must declare Automatic start, task disable, and failure recovery."""
    plan = run_dry_run(ROOT)
    assert plan.get("ok") is True
    assert plan.get("service_name") == "BobJeeves"
    # Explicit K6 fields (not only prose in steps)
    assert str(plan.get("start_type") or "").lower() in ("auto", "automatic")
    assert plan.get("disable_task") is True
    assert plan.get("disable_task_name") == "BobJeeves-chair"
    assert plan.get("recovery_restart") is True
    assert plan.get("no_bobircd_dependency") is True
    steps = " ".join(str(s) for s in (plan.get("steps") or []))
    assert "BobJeeves-chair" in steps
    assert "start= auto" in steps.lower() or "automatic" in steps.lower()


def test_k6_apply_source_disables_task_and_sets_recovery():
    """Installer source must actually Disable-ScheduledTask / sc failure on Apply."""
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Disable-ScheduledTask" in text or "Unregister-ScheduledTask" in text
    assert "BobJeeves-chair" in text
    # recovery: sc failure / reset / actions= restart
    assert "sc.exe failure" in text.lower() or "failure " in text.lower()
    assert "start= auto" in text
    # must not leave service Disabled
    assert "start= disabled" not in text.lower()


def test_k6_skill_documents_service_over_task():
    skill = ROOT / "skills" / "jeeves-install-service" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "BobJeeves-chair" in text
    assert "Disable" in text or "disable" in text
    assert "Automatic" in text or "start= auto" in text or "auto" in text.lower()
