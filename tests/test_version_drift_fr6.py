"""FR #6 / K5: tagged release deploy identity + webhook version + drift check."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from jeeves.receiver import StubReceiver
from jeeves.versioning import (
    check_drift,
    normalize_tag,
    running_version,
    version_report_payload,
    write_version_stamp,
)

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_and_package_present():
    assert (ROOT / "VERSION").is_file()
    ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0]
    assert ver
    assert normalize_tag(ver).startswith("v")
    info = running_version(ROOT)
    assert info.package
    assert info.release_tag
    assert info.report_string


def test_drift_match_when_expected_equals_running(monkeypatch):
    monkeypatch.setenv("JEEVES_RELEASE_TAG", "v0.2.0")
    monkeypatch.setenv("JEEVES_EXPECTED_TAG", "v0.2.0")
    d = check_drift(root=ROOT, allow_dirty=True)
    assert d.drifted is False
    assert d.ok is True
    assert d.reason == "match"


def test_drift_detects_tag_mismatch(monkeypatch):
    monkeypatch.setenv("JEEVES_RELEASE_TAG", "v0.1.0")
    monkeypatch.setenv("JEEVES_EXPECTED_TAG", "v9.9.9")
    d = check_drift(root=ROOT, allow_dirty=True)
    assert d.drifted is True
    assert d.ok is False
    assert d.reason == "tag_mismatch"


def test_version_report_payload_shape(monkeypatch):
    monkeypatch.setenv("JEEVES_RELEASE_TAG", "v0.2.0")
    monkeypatch.setenv("JEEVES_EXPECTED_TAG", "v0.2.0")
    p = version_report_payload(ROOT)
    assert "version" in p
    assert "jeeves_version" in p
    assert "version_drift" in p
    assert p["version_drift"]["drifted"] is False


def test_webhook_snapshot_includes_version(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JEEVES_RELEASE_TAG", "v0.2.0")
    monkeypatch.setenv("JEEVES_EXPECTED_TAG", "v0.2.0")
    home = tmp_path / "digest"
    home.mkdir()
    write_version_stamp(home, ROOT)
    rx = StubReceiver(home)
    port = rx.start()
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/bob/v1/report", timeout=5) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        assert "version" in doc
        assert "jeeves_version" in doc
        assert "version_drift" in doc
        assert doc["version"].startswith("v")
    finally:
        rx.stop()


def test_deploy_script_dry_run_json():
    script = ROOT / "tools" / "Deploy-BobJeevesRelease.ps1"
    assert script.is_file()
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Tag",
            "v0.2.0",
            "-DryRun",
            "-Json",
            "-RepoRoot",
            str(ROOT),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"err={err!r} out={out!r}"
    i = out.find("{")
    assert i >= 0, out
    plan = json.loads(out[i:])
    assert plan.get("dry_run") is True
    assert plan.get("tag") == "v0.2.0"
    assert plan.get("never_touch_ircd") is True
    assert plan.get("ok") is True
    steps = " ".join(str(s) for s in (plan.get("steps") or []))
    assert "git clone" in steps.lower() or "clone" in steps.lower()
    assert "drift" in steps.lower()


def test_deploy_requires_tag():
    script = ROOT / "tools" / "Deploy-BobJeevesRelease.ps1"
    # empty tag and hide VERSION by using temp root without VERSION
    tmp = Path(os.environ.get("TEMP") or ".") / f"jeeves-notag-{os.getpid()}"
    tmp.mkdir(exist_ok=True)
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Tag",
                "",
                "-DryRun",
                "-Json",
                "-RepoRoot",
                str(tmp),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        i = out.find("{")
        if i >= 0:
            plan = json.loads(out[i:])
            assert plan.get("ok") is False
        assert proc.returncode == 2
    finally:
        pass


def test_release_skill_complete():
    skill = (ROOT / "skills" / "jeeves-release" / "SKILL.md").read_text(encoding="utf-8")
    assert "TODO: seed FR" not in skill
    assert "Deploy-BobJeevesRelease.ps1" in skill
    assert "drift" in skill.lower()
    assert "JEEVES_RELEASE_TAG" in skill


def test_dry_run_main_includes_version():
    proc = subprocess.run(
        ["python", "-m", "jeeves", "dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert "version" in doc
    assert "version_drift" in doc
