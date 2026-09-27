"""Ghost busy heal: busy workers with no accepted row must not block !bored.

Evidence 2026-09-27: ionos-14020 ACK MRB gh-Jeeves#202; BobJeeves restarted;
DONE missed on the wire; bored_skip reason=busy; FR #204 went to marchhare.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.queue import load_queue, save_queue, worker_state
from jeeves.resync import reconcile_queue


def test_decide_heals_busy_without_accepted(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#204",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/204",
                    "seq": 1,
                    "line": "next",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {
                "ionos-14020": {
                    "state": "busy",
                    "job": "SimonBarnett/gh-Jeeves MRB #202",
                    "ts": "2026-09-27T19:30:46Z",
                }
            },
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "ionos-14020", "#ionos")
    assert d.action == "assign"
    assert d.line and "FR SimonBarnett/gh-Jeeves#204" in d.line
    assert worker_state(home, "ionos-14020") == "idle"


def test_decide_keeps_busy_when_accepted_present(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#204",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/204",
                    "seq": 1,
                }
            ],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "MRB",
                    "id": "#202",
                    "nick": "ionos-14020",
                    "channel": "#ionos",
                    "seq": 2,
                }
            ],
            "done": [],
            "workers": {
                "ionos-14020": {"state": "busy", "ts": "2026-09-27T19:30:46Z"}
            },
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "ionos-14020", "#ionos")
    assert d.action == "busy"
    assert d.reason == "busy"
    assert worker_state(home, "ionos-14020") == "busy"


def test_resync_idles_connected_nick_when_accepted_finished(tmp_path: Path):
    """Connected seat holds accepted MRB that GitHub no longer lists → clear busy."""
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "MRB",
                    "id": "#202",
                    "nick": "ionos-14020",
                    "channel": "#ionos",
                    "seq": 1,
                    "line": "merged",
                }
            ],
            "done": [],
            "workers": {
                "ionos-14020": {
                    "state": "busy",
                    "job": "SimonBarnett/gh-Jeeves MRB #202",
                    "ts": "2026-09-27T19:30:46Z",
                }
            },
        },
    )
    # Desired empty: PR merged / closed — nothing outstanding for this identity.
    stats = reconcile_queue(
        home,
        desired=[],
        connected_nicks={"ionos-14020"},
        release_orphans=True,
    )
    assert stats.removed >= 1
    doc = load_queue(home)
    assert doc.get("accepted") == []
    workers = doc.get("workers") or {}
    assert "ionos-14020" not in workers or workers["ionos-14020"].get("state") != "busy"


def test_heal_then_second_seat_respects_exclusive_offer(tmp_path: Path):
    """After ghost heal + assign, the same row_key stays locked for 90s."""
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#204",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/204",
                    "seq": 1,
                    "line": "next",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {
                "ionos-14020": {"state": "busy", "ts": "2026-09-27T19:30:46Z"}
            },
        },
    )
    st = ChairAssignState(timeout_s=90.0)
    st.bind(home)
    now = 1_000_000.0
    d1 = st.decide(
        home,
        "ionos-14020",
        "#ionos",
        live_nicks={"ionos-14020", "marchhare-14764"},
        now=now,
    )
    assert d1.action == "assign"
    d2 = st.decide(
        home,
        "marchhare-14764",
        "#marchhare",
        live_nicks={"ionos-14020", "marchhare-14764"},
        now=now + 10,
    )
    assert d2.action == "nothing"
    assert d2.reason == "none_eligible"


def test_heal_does_not_run_when_worker_idle(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#204",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/204",
                    "seq": 1,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {"ionos-14020": {"state": "idle", "ts": "2026-09-27T19:30:46Z"}},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "ionos-14020", "#ionos")
    assert d.action == "assign"
    assert worker_state(home, "ionos-14020") == "idle"
