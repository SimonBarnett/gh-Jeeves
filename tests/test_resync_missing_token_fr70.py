"""FR #70: missing/invalid GitHub token must not exit(2) the chair+receiver.

FR #49 / CAST IRON: report once, keep running without resync. Announce,
queue, ACK/DONE and the receiver never depend on the token.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jeeves.resync import (
    DiffStats,
    FakeGitHub,
    ResyncConfigError,
    build_resync_scheduler,
    run_resync,
)


def _clear_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "GITHUB_TOKEN",
        "JEEVES_GITHUB_TOKEN",
        "JEEVES_GITHUB_TOKEN_FILE",
        "JEEVES_RESYNC_DISABLE",
    ):
        monkeypatch.delenv(k, raising=False)


def test_main_missing_token_keeps_receiver_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """Acceptance: resync enabled, no token → stay up; GET digest works; one warn."""
    _clear_token_env(monkeypatch)
    jh = tmp_path / "jeeves"
    dh = tmp_path / "digest"
    jh.mkdir()
    dh.mkdir()
    monkeypatch.setenv("JEEVES_HOME", str(jh))
    monkeypatch.setenv("BOB_DIGEST_HOME", str(dh))
    monkeypatch.setenv("JEEVES_CHAIR_NO_IRC", "1")
    monkeypatch.setenv("JEEVES_CHAIR_SMOKE_SECONDS", "4")
    # Free port for receiver
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    from jeeves import __main__ as jeeves_main

    holder: dict = {"rc": None, "exc": None}

    def run() -> None:
        try:
            holder["rc"] = jeeves_main.main(
                [
                    "all",
                    "--receiver-bind",
                    "127.0.0.1",
                    "--receiver-port",
                    str(port),
                    "--jeeves-home",
                    str(jh),
                    "--digest-home",
                    str(dh),
                    "--no-singleton",
                    "--resync-interval",
                    "3600",
                ]
            )
        except Exception as e:
            holder["exc"] = e

    t = threading.Thread(target=run, name="jeeves-fr70", daemon=True)
    t.start()
    # Wait for receiver
    deadline = time.time() + 8
    body = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/bob/v1/report", timeout=1) as resp:
                body = resp.read().decode("utf-8")
                assert resp.status == 200
                break
        except Exception:
            time.sleep(0.1)
    assert body is not None, "receiver never answered GET /bob/v1/report"
    json.loads(body)  # valid digest JSON
    # Still up while smoke window open (or clean exit 0 after)
    time.sleep(0.2)
    out = capsys.readouterr().out
    combined = out
    # Wait for smoke exit
    t.join(timeout=8)
    out += capsys.readouterr().out
    combined = out
    assert holder["exc"] is None, holder["exc"]
    assert holder["rc"] == 0, f"expected clean exit 0, got rc={holder['rc']} out={combined[-800:]}"
    warn_lines = [
        ln
        for ln in combined.splitlines()
        if ln.startswith("WARN resync") and "token" in ln.lower()
    ]
    assert len(warn_lines) == 1, f"expected exactly one WARN resync line, got {warn_lines!r} in {combined[-1200:]}"
    fatal = [ln for ln in combined.splitlines() if ln.startswith("ERROR resync config:")]
    assert fatal == [], f"must not print fatal ERROR then exit: {fatal}"


def test_wire_resync_missing_token_returns_none_with_one_warning(tmp_path: Path, monkeypatch, capsys):
    """Helper used by main: missing token → sched None, one WARN, no raise to exit."""
    _clear_token_env(monkeypatch)
    home = tmp_path / "d"
    home.mkdir()
    from jeeves.__main__ import wire_resync_scheduler

    sched, msg = wire_resync_scheduler(home, interval_s=60.0, jeeves_home=tmp_path / "j")
    assert sched is None
    assert msg and "token" in msg.lower()
    # second call should not duplicate if we track — at least helper is idempotent warn once at main


def test_invalid_token_401_skips_resync_keeps_queue(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    from jeeves.queue import save_queue, load_queue

    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [{"repo": "o/r", "task": "FR", "id": "#1", "seq": 1, "line": "keep"}],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    gh = FakeGitHub(repos=["o/r"], auth_failed=True)
    st = run_resync(home, gh)
    assert st.skipped_github_down or st.skipped_auth
    assert load_queue(home)["unaccepted"][0]["id"] == "#1"


def test_installer_documents_resync_disable_and_token_prompt():
    root = Path(__file__).resolve().parents[1]
    text = (root / "tools" / "Install-BobJeeves.ps1").read_text(encoding="utf-8")
    assert "ResyncDisable" in text
    assert "PromptGitHubToken" in text or "GitHubToken" in text
    assert "JEEVES_RESYNC_DISABLE" in text
    assert "github.token" in text.lower() or "JEEVES_GITHUB_TOKEN_FILE" in text
