"""FR #105: startup resync must not wipe accepted jobs before NAMES/grace.

Boot with empty connected_nicks was releasing every accepted row. Defer orphan
release until membership is ready AND grace has elapsed; keep workers in sync.
"""
from __future__ import annotations

import time
from pathlib import Path

from jeeves.digest import apply_report, load_digest, public_digest_snapshot
from jeeves.queue import load_queue, save_queue
from jeeves.resync import DiffStats, ResyncScheduler, reconcile_queue, run_resync
from jeeves.resync import FakeGitHub


def _seed_accepted(home: Path) -> None:
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#70",
                    "nick": "marchhare-42356",
                    "channel": "#marchhare",
                    "seq": 1,
                    "line": "dead seat",
                },
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "MRB",
                    "id": "#101",
                    "nick": "flamingo-43052",
                    "channel": "#flamingo",
                    "seq": 2,
                    "line": "live",
                },
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "MRB",
                    "id": "#100",
                    "nick": "ionos-9504",
                    "channel": "#ionos",
                    "seq": 3,
                    "line": "live",
                },
            ],
            "done": [],
            "workers": {
                "flamingo-43052": {"state": "busy", "job": "SimonBarnett/gh-Jeeves MRB #101", "ts": "2026-09-25T19:00:00Z"},
                "ionos-9504": {"state": "busy", "job": "SimonBarnett/gh-Jeeves MRB #100", "ts": "2026-09-25T19:00:00Z"},
                "marchhare-42356": {"state": "busy", "job": "SimonBarnett/gh-Jeeves FR #70", "ts": "2026-09-25T10:00:00Z"},
            },
        },
    )
    for nick, job in (
        ("flamingo-43052", "SimonBarnett/gh-Jeeves MRB #101"),
        ("ionos-9504", "SimonBarnett/gh-Jeeves MRB #100"),
        ("marchhare-42356", "SimonBarnett/gh-Jeeves FR #70"),
    ):
        apply_report(home, {"op": "worker_state", "nick": nick, "state": "busy", "job": job})


def test_boot_resync_empty_membership_keeps_accepted(tmp_path: Path):
    """Acceptance: boot resync before NAMES leaves accepted unchanged."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_accepted(home)
    desired = [
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "FR",
            "id": "#70",
            "line": "dead seat",
            "seq": 1,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#101",
            "line": "live",
            "seq": 2,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#100",
            "line": "live",
            "seq": 3,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "FR",
            "id": "#999",
            "line": "new",
            "seq": 9,
            "url": "",
        },
    ]
    stats = reconcile_queue(
        home,
        desired,
        connected_nicks=set(),  # pre-NAMES empty
        release_orphans=False,
    )
    assert stats.accepted_released == 0
    doc = load_queue(home)
    nicks = {r["nick"] for r in doc["accepted"]}
    assert nicks == {"marchhare-42356", "flamingo-43052", "ionos-9504"}
    # unaccepted can still gain new items
    assert any(r.get("id") == "#999" for r in doc["unaccepted"])


def test_dead_nick_released_only_after_orphans_enabled(tmp_path: Path):
    """marchhare-42356 absent from NAMES → released once orphan release is on."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_accepted(home)
    desired = [
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "FR",
            "id": "#70",
            "line": "dead seat",
            "seq": 1,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#101",
            "line": "live",
            "seq": 2,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#100",
            "line": "live",
            "seq": 3,
            "url": "",
        },
    ]
    live = {"flamingo-43052", "ionos-9504"}
    stats = reconcile_queue(home, desired, connected_nicks=live, release_orphans=True)
    assert stats.accepted_released == 1
    doc = load_queue(home)
    nicks = {r.get("nick") for r in doc["accepted"]}
    assert "marchhare-42356" not in nicks
    assert "flamingo-43052" in nicks and "ionos-9504" in nicks
    # FR #70 back to unaccepted
    assert any(r.get("id") == "#70" for r in doc["unaccepted"])
    # workers: dead nick idled/removed; live stay busy
    workers = doc.get("workers") or {}
    assert "marchhare-42356" not in workers or workers["marchhare-42356"].get("state") == "idle"
    assert workers.get("flamingo-43052", {}).get("state") == "busy"
    assert workers.get("ionos-9504", {}).get("state") == "busy"


def test_busy_worker_matches_accepted_after_resync(tmp_path: Path):
    """Every busy worker has accepted row; every accepted nick is busy."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_accepted(home)
    desired = [
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#101",
            "line": "live",
            "seq": 2,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#100",
            "line": "live",
            "seq": 3,
            "url": "",
        },
        {
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "FR",
            "id": "#70",
            "line": "dead",
            "seq": 1,
            "url": "",
        },
    ]
    reconcile_queue(
        home,
        desired,
        connected_nicks={"flamingo-43052", "ionos-9504"},
        release_orphans=True,
    )
    doc = load_queue(home)
    accepted_nicks = {str(r.get("nick") or "") for r in doc["accepted"] if r.get("nick")}
    busy = {
        n
        for n, e in (doc.get("workers") or {}).items()
        if isinstance(e, dict) and str(e.get("state") or "").lower() == "busy"
    }
    assert accepted_nicks == busy
    assert "marchhare-42356" not in busy


def test_scheduler_defers_orphan_release_until_ready(tmp_path: Path, monkeypatch):
    home = tmp_path / "d"
    home.mkdir()
    _seed_accepted(home)
    gh = FakeGitHub(
        repos=["SimonBarnett/gh-Jeeves"],
        issues={"SimonBarnett/gh-Jeeves": []},
        pulls={
            "SimonBarnett/gh-Jeeves": [
                {
                    "number": 101,
                    "title": "x",
                    "body": "",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/pull/101",
                    "created_at": "2024-01-01T00:00:00Z",
                    "merged": False,
                    "state": "open",
                },
                {
                    "number": 100,
                    "title": "y",
                    "body": "",
                    "html_url": "https://github.com/SimonBarnett/gh-Jeeves/pull/100",
                    "created_at": "2024-01-01T00:00:00Z",
                    "merged": False,
                    "state": "open",
                },
            ]
        },
        closed_pulls={"SimonBarnett/gh-Jeeves": []},
    )
    ready = {"v": False}
    sched = ResyncScheduler(
        home,
        gh,
        connected_nicks_fn=lambda: set(),
        membership_ready_fn=lambda: ready["v"],
        orphan_grace_s=600.0,
    )
    # Force "just booted"
    sched._boot_ts = time.time()
    st = sched.run_once()
    assert st.accepted_released == 0
    doc = load_queue(home)
    assert len(doc["accepted"]) == 3

    # Membership ready but grace not elapsed → still hold
    ready["v"] = True
    sched._boot_ts = time.time()
    st2 = sched.run_once()
    assert st2.accepted_released == 0
    assert len(load_queue(home)["accepted"]) == 3

    # Ready + past grace + empty NAMES → release all (truly absent)
    sched._boot_ts = time.time() - 601
    st3 = sched.run_once()
    assert st3.accepted_released >= 1
    assert load_queue(home)["accepted"] == [] or all(
        r.get("nick") in set() for r in load_queue(home)["accepted"]
    )
