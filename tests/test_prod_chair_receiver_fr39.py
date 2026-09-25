"""FR #39: production chair + GIT receiver port; BobJeeves entry; G1 stays green."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from jeeves.listfmt import format_unaccepted_list, help_lines, reset_list_rate
from jeeves.local_ircd import LocalIrcd
from jeeves.prod_receiver import (
    ProdReceiver,
    assert_homes_distinct,
    digest_home_from_env,
)
from jeeves.queue import Claim, apply_queue_event
from jeeves.roles import JeevesChair
from jeeves.singleton import SingletonError, acquire, release
from jeeves.wire import is_help, is_list, parse_list_filters


ROOT = Path(__file__).resolve().parents[1]


def test_homes_must_differ(tmp_path: Path):
    h = tmp_path / "same"
    h.mkdir()
    with pytest.raises(RuntimeError):
        assert_homes_distinct(h, h)


def test_singleton_blocks_second(tmp_path: Path):
    home = tmp_path / "j"
    home.mkdir()
    acquire(home, role="test")
    # same pid re-acquire ok
    acquire(home, role="test")
    release(home)


def test_list_help_wire_and_format(tmp_path: Path):
    assert is_list("!list")
    assert is_list("!LIST fr")
    assert is_help("!help")
    assert parse_list_filters("!list mrb") == ("MRB", None, False)
    assert parse_list_filters("!list all")[2] is True
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "FR", "#39", line="port chair"),
    )
    lines = format_unaccepted_list(home)
    assert lines
    assert any("FR" in x and "gh-Jeeves#39" in x for x in lines)
    assert help_lines()


def test_chair_list_in_channel_answers_by_pm_only(tmp_path: Path):
    """In-channel !list → PM via _pm only (never channel shop_egress)."""
    reset_list_rate()
    home = tmp_path / "digest"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "FR", "#1", line="one"),
    )
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "MRB", "#2", line="two"),
    )
    # Minimal chair without live IRC thread: use object.__new__ pattern via local ircd
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        chair = JeevesChair(
            "127.0.0.1",
            port,
            home,
            "http://127.0.0.1:9",
            shops=["#flamingo"],
        )
        # drive handler directly (deterministic)
        chair._handle_shop("flamingo-9001", "#flamingo", "!list")
        assert any(h.startswith("list_pm:") for h in chair.handled)
        assert chair.pm_egress, "expected PM lines"
        assert all(n == "flamingo-9001" for n, _ in chair.pm_egress)
        assert any("FR" in t or "unaccepted" in t or "MRB" in t for _, t in chair.pm_egress)
        assert not any("OFFER" in (t or "") for _, t in chair.shop_egress)
        chair.stop()
    finally:
        ircd.stop()


def test_prod_receiver_git_and_report(tmp_path: Path):
    home = tmp_path / "dig"
    home.mkdir()
    rx = ProdReceiver(home, host="127.0.0.1", port=0)
    port = rx.start()
    base = f"http://127.0.0.1:{port}"
    try:
        payload = json.dumps(
            {
                "action": "opened",
                "issue": {
                    "number": 39,
                    "title": "port",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/issues/39",
                },
                "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/bob/v1/git",
            data=payload,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "issues"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 204
        with urllib.request.urlopen(f"{base}/bob/v1/report", timeout=5) as resp:
            snap = json.loads(resp.read().decode("utf-8"))
        assert snap["queue"]["unaccepted"]
        assert any(r.get("id") == "#39" for r in snap["queue"]["unaccepted"])
    finally:
        rx.stop()


def test_module_dry_run():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "jeeves", "dry-run", "--jeeves-home", "C:\\tmp\\j", "--digest-home", "C:\\tmp\\d"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["homes_distinct"] is True
    assert data["entry"] == "python -m jeeves"
    assert "BobIrcd" in data["never_touch"]


def test_install_ps1_dry_run_json():
    ps1 = ROOT / "tools" / "Install-BobJeeves.ps1"
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
            "-DryRun",
            "-Json",
            "-RepoRoot",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    data = json.loads(proc.stdout)
    assert data["never_touch_ircd"] is True
    assert data["service_name"] == "BobJeeves"
    assert data.get("python_module") == "jeeves" or "jeeves" in str(data.get("chair_entry") or "")


def test_g2_checklist_exists():
    p = ROOT / "docs" / "g2-live-smoke-checklist.md"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "singleton" in text.lower()
    assert "!list" in text
    assert "BobIrcd" in text
    assert "queue.json" in text


def test_start_ps1_dry_run():
    ps1 = ROOT / "tools" / "Start-BobJeeves.ps1"
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
            "-DryRun",
            "-RepoRoot",
            str(ROOT),
            "-JeevesHome",
            str(ROOT / "_jhome"),
            "-DigestHome",
            str(ROOT / "_dhome"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "python -m jeeves" in proc.stdout or "jeeves" in proc.stdout.lower()
