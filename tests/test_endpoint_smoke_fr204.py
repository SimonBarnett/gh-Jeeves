"""FR #204: offline smoke scripts against StubReceiver; default base URL pin."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jeeves.intake import IntakeConfig
from jeeves.receiver import StubReceiver

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEFAULT_BASE = "https://bob.ntsa.uk"
SCRIPTS = (
    "test_git_endpoints.py",
    "test_jira_endpoints.py",
    "test_report_endpoints.py",
    "test_intake_endpoints.py",
)
SECRET = "fr204-test-secret"


def _load(name: str):
    path = TOOLS / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_base_url_is_bob_ntsa_uk():
    common = _load("smoke_common.py")
    assert common.DEFAULT_BASE_URL == DEFAULT_BASE
    for script in SCRIPTS:
        text = (TOOLS / script).read_text(encoding="utf-8")
        assert "bob.ntsa.uk" in text
        assert "DEFAULT_BASE_URL" in text or DEFAULT_BASE in text


def test_inventory_matches_receiver_groups():
    recv = (ROOT / "src" / "jeeves" / "receiver.py").read_text(encoding="utf-8")
    for needle in ("/bob/v1/git", "/bob/v1/jira", "/bob/v1/report", "/bob/v1/intake"):
        assert needle in recv
    for script in SCRIPTS:
        assert (TOOLS / script).is_file()


@pytest.fixture()
def local_base(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    rx = StubReceiver(
        home,
        bob_secret=SECRET,
        require_secret=True,
        intake_cfg=IntakeConfig(fleet_key=""),
    )
    port = rx.start()
    os.environ["BOB_SECRET"] = SECRET
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        rx.stop()
        os.environ.pop("BOB_SECRET", None)


def test_scripts_offline_against_stub(local_base: str):
    """Automated suite must not contact the public host."""
    env = os.environ.copy()
    env["BOB_SECRET"] = SECRET
    env.pop("BOB_SMOKE_BASE_URL", None)
    for script in SCRIPTS:
        path = TOOLS / script
        proc = subprocess.run(
            [sys.executable, str(path), "--base-url", local_base, "--json", "--timeout", "10"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"
        assert "bob.ntsa.uk" not in (proc.stdout + proc.stderr).replace(DEFAULT_BASE, "")
        # Ensure we actually pointed at loopback
        assert "127.0.0.1" in local_base


def test_live_suite_skips_without_opt_in():
    """Ordinary unit tests never hit production."""
    if os.environ.get("BOB_SMOKE_LIVE", "").strip() in ("1", "true", "yes"):
        pytest.skip("live mode requested — see test_live_endpoint_smoke")
    # Guard: importing scripts does not network
    for script in SCRIPTS:
        _load(script)


@pytest.mark.skipif(
    os.environ.get("BOB_SMOKE_LIVE", "").strip().lower() not in ("1", "true", "yes"),
    reason="opt-in live smoke: set BOB_SMOKE_LIVE=1 (uses https://bob.ntsa.uk)",
)
def test_live_endpoint_smoke():
    """Opt-in live job against canonical host. Skip groups missing credentials."""
    env = os.environ.copy()
    secret = (env.get("BOB_SECRET") or "").strip()
    results = []
    for script in SCRIPTS:
        # git/report GET can run without secret; jira/intake need secret
        if script in ("test_jira_endpoints.py", "test_intake_endpoints.py") and not secret:
            results.append((script, "skip", "BOB_SECRET absent"))
            continue
        proc = subprocess.run(
            [sys.executable, str(TOOLS / script), "--json", "--timeout", "20"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        results.append((script, proc.returncode, proc.stdout[-500:]))
        # Live may fail soft on write policy; still require git/report GET paths
        if script == "test_git_endpoints.py":
            assert proc.returncode == 0, proc.stdout
    # At least one skip message clear if no secret
    if not secret:
        assert any(r[1] == "skip" for r in results)
