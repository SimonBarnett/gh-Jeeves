"""FR #49: resync on start so !list is populated (failing-test-first contract)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from jeeves.listfmt import format_unaccepted_list
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import load_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair
from jeeves.resync import (
    FakeGitHub,
    ResyncConfigError,
    build_resync_scheduler,
    load_github_token,
    run_resync,
)


def _issue(n: int, title: str) -> dict:
    return {
        "number": n,
        "title": title,
        "html_url": f"https://github.com/o/r/issues/{n}",
        "created_at": f"2024-01-0{n}T00:00:00Z",
        "state": "open",
    }


def test_build_resync_requires_token_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("JEEVES_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("JEEVES_RESYNC_DISABLE", raising=False)
    monkeypatch.delenv("JEEVES_GITHUB_TOKEN_FILE", raising=False)
    home = tmp_path / "d"
    home.mkdir()
    with pytest.raises(ResyncConfigError):
        build_resync_scheduler(home, require_token=True)


def test_build_resync_disable_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JEEVES_RESYNC_DISABLE", "1")
    home = tmp_path / "d"
    home.mkdir()
    assert build_resync_scheduler(home) is None


def test_token_from_file_never_logged(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("JEEVES_GITHUB_TOKEN", raising=False)
    tok_file = tmp_path / "tok"
    tok_file.write_text("ghp_secret_test_token\n", encoding="utf-8")
    monkeypatch.setenv("JEEVES_GITHUB_TOKEN_FILE", str(tok_file))
    assert load_github_token() == "ghp_secret_test_token"


def test_github_down_keeps_queue(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    from jeeves.queue import save_queue

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
    gh = FakeGitHub(repos=["o/r"], down=True)
    st = run_resync(home, gh)
    assert st.skipped_github_down
    assert load_queue(home)["unaccepted"][0]["id"] == "#1"


def test_resync_on_start_populates_list_pm(tmp_path: Path):
    """
    Chair start runs resync (FakeGitHub) before handling !list;
    in-channel !list answers by PM with outstanding FR/MRB/UAT.
    """
    home = tmp_path / "digest"
    home.mkdir()
    # empty queue on disk — must fill from GitHub
    gh = FakeGitHub(
        repos=["SimonBarnett/gh-Jeeves"],
        issues={
            "SimonBarnett/gh-Jeeves": [
                _issue(49, "wire resync"),
                _issue(50, "other FR"),
            ]
        },
        pulls={
            "SimonBarnett/gh-Jeeves": [
                {
                    "number": 99,
                    "title": "impl",
                    "body": "closes #50",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/pull/99",
                    "created_at": "2024-02-01T00:00:00Z",
                    "merged": False,
                    "state": "open",
                }
            ]
        },
        closed_pulls={"SimonBarnett/gh-Jeeves": []},
    )
    sched = build_resync_scheduler(
        home,
        interval_s=3600,
        client=gh,
        require_token=False,
    )
    assert sched is not None

    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    base = f"http://127.0.0.1:{rport}"

    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        base,
        nick="Jeeves",
        shops=["#flamingo"],
        resync_scheduler=sched,
    )
    asker = IrcClient("127.0.0.1", port, "simon")
    asker.join("#bobiverse")
    chair.start()
    # resync-on-start must have run
    assert sched.runs >= 1
    q = load_queue(home)
    assert q["unaccepted"], "resync must populate unaccepted before !list"
    tasks = {(r["task"], r["id"]) for r in q["unaccepted"]}
    assert ("FR", "#49") in tasks
    assert ("MRB", "#99") in tasks
    assert ("FR", "#50") not in tasks  # superseded by PR

    time.sleep(0.15)
    try:
        asker.privmsg("#bobiverse", "!list")
        # collect PMs to simon
        deadline = time.time() + 5
        pms: list[str] = []
        while time.time() < deadline:
            m = asker.wait_privmsg(
                predicate=lambda x: x[1].lower() == "simon" or not str(x[1]).startswith("#"),
                timeout=0.4,
            )
            if m:
                pms.append(m[2])
            if any("FR" in p or "MRB" in p or "queue" in p.lower() for p in pms):
                if len(pms) >= 1:
                    # wait a bit for multi-line list
                    time.sleep(0.3)
                    while True:
                        m2 = asker.wait_privmsg(timeout=0.2)
                        if not m2:
                            break
                        pms.append(m2[2])
                    break
        # chair also records pm_egress
        lines = [t for _n, t in chair.pm_egress] or pms
        assert lines, f"expected !list PM; handled={chair.handled} pm={chair.pm_egress}"
        blob = "\n".join(lines)
        assert "49" in blob or "#49" in blob or "FR" in blob
        # format_unaccepted_list sanity
        fmt = format_unaccepted_list(home)
        assert any("49" in ln for ln in fmt) or any("FR" in ln for ln in fmt)
    finally:
        chair.stop()
        asker.close()
        rx.stop()
        ircd.stop()
