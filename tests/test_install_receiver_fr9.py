"""FR #9 / K8: BobReport receiver service + installer owned in gh-Jeeves."""

from __future__ import annotations

import re
from pathlib import Path

from jeeves import install_receiver as ir

ROOT = Path(__file__).resolve().parents[1]


def test_receiver_installer_files_exist():
    missing = ir.installer_paths_ok(ROOT)
    assert missing == [], f"missing in-repo receiver install surface: {missing}"


def test_install_bobreport_never_touches_ircd():
    text = (ROOT / "tools" / "Install-BobReport.ps1").read_text(encoding="utf-8")
    assert "BobReport" in text
    assert "DryRun" in text or "-DryRun" in text
    assert not re.search(r"(?i)&\s*sc\.exe\s+\w+\s+BobIrcd", text)
    assert ir.installer_never_touches_ircd(ROOT / "tools" / "Install-BobReport.ps1") == []
    assert "BobReport-ionos" in text or "ad-hoc" in text.lower() or "ad hoc" in text.lower()


def test_start_bobreport_repo_owned_not_profile_path():
    text = (ROOT / "tools" / "Start-BobReport.ps1").read_text(encoding="utf-8")
    assert "python -m jeeves" in text or "jeeves receiver" in text
    assert "Administrator" not in text or "not" in text.lower()
    # must not hard-code only profile long-running path as the engine
    assert r".grok\long-running-background-tasks\Start-BobReport-ionos" not in text


def test_dry_run_install_bobreport_json():
    plan = ir.run_install_dry_run(ROOT)
    assert plan.get("_exit_code") == 0
    assert plan.get("dry_run") is True
    assert plan.get("apply") is False
    assert plan.get("service_name") == "BobReport"
    assert plan.get("never_touch_ircd") is True
    assert plan.get("ok") is True
    assert plan.get("port") == 19781 or plan.get("port") == "19781" or int(plan.get("port") or 0) == 19781
    assert "127.0.0.1" in str(plan.get("bind"))
    steps = " ".join(str(s) for s in (plan.get("steps") or []))
    assert "BOB_DIGEST_HOME" in steps or "digest" in steps.lower()
    forbidden = plan.get("forbidden") or []
    assert any("BobReport-ionos" in str(x) or "ad hoc" in str(x).lower() or "ad-hoc" in str(x).lower() for x in forbidden) or plan.get("replaces")


def test_dry_run_start_bobreport():
    data = ir.run_start_dry_run(ROOT)
    assert data.get("_exit_code") == 0
    assert data.get("dry_run") is True
    assert "Ergo" in (data.get("never_touch") or [])
    cmd = str(data.get("command") or "")
    assert "jeeves" in cmd.lower() and "receiver" in cmd.lower()


def test_skill_documents_bobreport():
    skill = (ROOT / "skills" / "jeeves-install-service" / "SKILL.md").read_text(encoding="utf-8")
    assert "Install-BobReport.ps1" in skill
    assert "BobReport" in skill
    assert "TODO: seed FR" not in skill
    assert "K8" in skill or "receiver" in skill.lower()


def test_main_supports_receiver_mode():
    main = (ROOT / "src" / "jeeves" / "__main__.py").read_text(encoding="utf-8")
    assert "receiver" in main
    assert "prod_receiver" in main or "ProdReceiver" in main
