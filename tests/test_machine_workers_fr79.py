"""FR #79: machines.<id>.workers mirrors top-level workers on ACK/DONE."""
from __future__ import annotations

import logging
from pathlib import Path

from jeeves.digest import (
    _unknown_machine_nicks_logged,
    apply_report,
    load_digest,
    public_digest_snapshot,
)


def test_worker_state_busy_fills_machines_flamingo(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "flamingo-43052"
    job = "SimonBarnett/gh-Jeeves FR #71"
    out = apply_report(home, {"op": "worker_state", "nick": nick, "state": "busy", "job": job})
    assert out.ok
    doc = load_digest(home)
    top = (doc.get("queue") or {}).get("workers") or {}
    assert top[nick]["state"] == "busy"
    assert top[nick]["job"] == job
    mw = ((doc.get("machines") or {}).get("flamingo") or {}).get("workers") or {}
    assert nick in mw
    assert mw[nick]["state"] == "busy"
    assert mw[nick]["job"] == job
    assert mw[nick].get("ts")
    # top-level twin agrees
    assert doc.get("workers", {}).get(nick, {}).get("state") == "busy"


def test_ack_then_done_flamingo_fills_and_idles(tmp_path: Path):
    """Acceptance: ACK then DONE for flamingo-43052 fills then idles machines.flamingo.workers."""
    home = tmp_path / "d"
    home.mkdir()
    nick = "flamingo-43052"
    job = "SimonBarnett/gh-Jeeves FR #71"
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "state": "busy",
            "job": job,
            "accepted_row": {"repo": "SimonBarnett/gh-Jeeves", "task": "FR", "id": "#71"},
        },
    )
    busy = load_digest(home)
    assert busy["workers"][nick]["state"] == "busy"
    assert busy["machines"]["flamingo"]["workers"][nick]["state"] == "busy"
    assert busy["machines"]["flamingo"]["workers"][nick]["job"] == job
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    done = load_digest(home)
    assert done["workers"][nick]["state"] == "idle"
    assert done["workers"][nick].get("job") is None
    assert done["machines"]["flamingo"]["workers"][nick]["state"] == "idle"
    assert done["machines"]["flamingo"]["workers"][nick].get("job") in (None, "")


def test_worker_state_idle_both_views(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "flamingo-43052"
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "busy", "job": "x"})
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "idle", "job": None})
    doc = load_digest(home)
    assert doc["queue"]["workers"][nick]["state"] == "idle"
    assert doc["machines"]["flamingo"]["workers"][nick]["state"] == "idle"
    assert doc["machines"]["flamingo"]["workers"][nick].get("job") in (None, "")


def test_queue_accept_and_done(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-42356"
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "state": "busy",
            "job": "y",
            "accepted_row": {"repo": "o/r", "task": "FR", "id": "#1"},
        },
    )
    assert load_digest(home)["machines"]["marchhare"]["workers"][nick]["state"] == "busy"
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    assert load_digest(home)["machines"]["marchhare"]["workers"][nick]["state"] == "idle"


def test_get_snapshot_heals(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(home, {"op": "worker_state", "nick": "ionos-99", "state": "busy", "job": "z"})
    # wipe machine map
    doc = load_digest(home)
    doc["machines"]["ionos"]["workers"] = {}
    from jeeves.digest import save_digest

    save_digest(home, doc)
    snap = public_digest_snapshot(home)
    assert snap["workers"]["ionos-99"]["state"] == "busy"
    assert snap["machines"]["ionos"]["workers"]["ionos-99"]["state"] == "busy"


def test_unknown_nick_no_crash_logs_once(tmp_path: Path, caplog):
    home = tmp_path / "d"
    home.mkdir()
    _unknown_machine_nicks_logged.clear()
    with caplog.at_level(logging.WARNING, logger="jeeves.digest"):
        out = apply_report(
            home, {"op": "worker_state", "nick": "not-a-worker", "state": "busy", "job": "x"}
        )
        assert out.ok
        apply_report(
            home, {"op": "worker_state", "nick": "not-a-worker", "state": "busy", "job": "y"}
        )
    doc = load_digest(home)
    assert "not-a-worker" in doc["queue"]["workers"]
    warn = [r for r in caplog.records if "worker_machine_unmapped" in r.getMessage()]
    assert len(warn) == 1


def test_delete_worker_clears_both(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(home, {"op": "worker_state", "nick": "flamingo-12", "state": "busy", "job": "j"})
    apply_report(home, {"op": "delete-worker", "machine": "flamingo", "pid": "12"})
    doc = load_digest(home)
    qw = (doc.get("queue") or {}).get("workers") or {}
    assert "flamingo-12" not in qw
    mw = (doc["machines"]["flamingo"].get("workers") or {})
    assert "flamingo-12" not in mw
    assert "12" not in mw


def test_ear_pid_merge_still_works(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(
        home,
        {
            "op": "merge",
            "machine": "flamingo",
            "online": True,
            "workers": {
                "46804": {
                    "nick": "flamingo-46804",
                    "state": "busy",
                    "working_on": "FR x",
                }
            },
        },
    )
    assert out.ok
    fl = load_digest(home)["machines"]["flamingo"]["workers"]
    # FR #162: ear pid rows promote to nick; no digit ghost key
    assert "flamingo-46804" in fl
    assert "46804" not in fl
    assert fl["flamingo-46804"]["state"] == "busy"
    assert fl["flamingo-46804"].get("working_on") == "FR x"
