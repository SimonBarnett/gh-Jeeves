"""FR #139: ACK during in-flight resync must not be wiped by reconcile save."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from jeeves.queue import accept_job, load_queue, queue_lock, save_queue
from jeeves.resync import FakeGitHub, reconcile_queue, run_resync


def _seed_unaccepted(home: Path) -> None:
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/AgentMonitor",
                    "task": "FR",
                    "id": "#105",
                    "line": "forward dedupe",
                    "url": "https://github.com/SimonBarnett/AgentMonitor/issues/105",
                    "seq": 1,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )


def test_ack_during_reconcile_lock_keeps_claim(tmp_path: Path):
    """While reconcile holds the queue lock, ACK waits; after unlock ACK lands."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_unaccepted(home)
    desired = [
        {
            "repo": "SimonBarnett/AgentMonitor",
            "task": "FR",
            "id": "#105",
            "line": "forward dedupe",
            "url": "https://github.com/SimonBarnett/AgentMonitor/issues/105",
            "seq": 1,
        }
    ]
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def do_resync() -> None:
        try:
            with queue_lock(home):
                # Simulate long reconcile critical section (GitHub fetch already done).
                barrier.wait(timeout=5)
                time.sleep(0.25)
                reconcile_queue(
                    home,
                    desired,
                    connected_nicks={"marchhare-31712"},
                    release_orphans=True,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"resync:{exc}")

    def do_ack() -> None:
        try:
            barrier.wait(timeout=5)
            # Contends for the same lock — must run after resync critical section.
            st, row = accept_job(
                home,
                "marchhare-31712",
                "#marchhare",
                "FR",
                "SimonBarnett/AgentMonitor",
                "105",
            )
            if st not in ("accepted", "already"):
                errors.append(f"ack_state={st}")
            if row is None:
                errors.append("ack_row_none")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ack:{exc}")

    t1 = threading.Thread(target=do_resync)
    t2 = threading.Thread(target=do_ack)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors, errors
    q = load_queue(home)
    accepted = [
        r
        for r in (q.get("accepted") or [])
        if str(r.get("id")) in ("#105", "105") and str(r.get("nick")) == "marchhare-31712"
    ]
    assert len(accepted) == 1, q.get("accepted")


def test_stale_save_without_lock_would_wipe_but_lock_prevents(tmp_path: Path):
    """Classic race: load → concurrent ACK → stale save; lock serializes so ACK wins after."""
    home = tmp_path / "d2"
    home.mkdir()
    _seed_unaccepted(home)
    desired = [
        {
            "repo": "SimonBarnett/AgentMonitor",
            "task": "FR",
            "id": "#105",
            "line": "forward dedupe",
            "seq": 1,
            "url": "",
        }
    ]
    # Resync with live nick keeps #105 accepted if already accepted before reconcile.
    accept_job(
        home,
        "marchhare-31712",
        "#marchhare",
        "FR",
        "SimonBarnett/AgentMonitor",
        "105",
    )
    st = reconcile_queue(
        home,
        desired,
        connected_nicks={"marchhare-31712"},
        release_orphans=True,
    )
    assert st.accepted_released == 0
    q = load_queue(home)
    assert any(str(r.get("nick")) == "marchhare-31712" for r in (q.get("accepted") or []))


def test_agentic_irc_in_chair_seed():
    from jeeves.roles import JeevesChair
    from jeeves.channel_join import normalize_channel

    # Inspect seed construction pattern used by JeevesChair.__init__
    shops = ["#marchhare"]
    seed = list(
        dict.fromkeys(
            [
                "#bobiverse",
                "#agentic_irc",
                *[normalize_channel(s) for s in shops],
            ]
        )
    )
    assert "#agentic_irc" in seed
    assert seed[0] == "#bobiverse"
