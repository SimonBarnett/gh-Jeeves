"""FR #79: machines.<id>.workers mirrors top-level workers on ACK/DONE."""
from __future__ import annotations
from pathlib import Path
from jeeves.digest import apply_report, load_digest, public_digest_snapshot

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
    apply_report(home, {"op": "queue_accept", "nick": nick, "state": "busy", "job": "y", "accepted_row": {"repo": "o/r", "task": "FR", "id": "#1"}})
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

def test_unknown_nick_no_crash(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(home, {"op": "worker_state", "nick": "not-a-worker", "state": "busy", "job": "x"})
    assert out.ok
    doc = load_digest(home)
    assert "not-a-worker" in doc["queue"]["workers"]

def test_delete_worker_clears_both(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(home, {"op": "worker_state", "nick": "flamingo-12", "state": "busy", "job": "j"})
    apply_report(home, {"op": "delete-worker", "machine": "flamingo", "pid": "12"})
    doc = load_digest(home)
    assert "flamingo-12" not in (doc.get("queue") or {}).get("workers") or {}
    mw = (doc["machines"]["flamingo"].get("workers") or {})
    assert "flamingo-12" not in mw
    assert "12" not in mw

def test_ear_pid_merge_still_works(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(home, {"op": "merge", "machine": "flamingo", "online": True, "workers": {"46804": {"nick": "flamingo-46804", "state": "busy", "working_on": "FR x"}}})
    assert out.ok
    fl = load_digest(home)["machines"]["flamingo"]["workers"]
    assert "46804" in fl