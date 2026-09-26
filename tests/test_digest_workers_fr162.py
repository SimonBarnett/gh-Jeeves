"""FR #162: digest has one nick-keyed entry per present worker, grouped by machine."""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import (
    apply_report,
    coerce_workers,
    load_digest,
    nick_keyed_machine_workers,
    prune_machine_worker_ghosts,
    public_digest_snapshot,
    save_digest,
)


def test_mirror_ack_writes_nick_only_no_pid_ghost(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-31712"
    apply_report(
        home,
        {"op": "worker_state", "nick": nick, "state": "busy", "job": "SimonBarnett/gh-Jeeves FR #162"},
    )
    doc = load_digest(home)
    mw = doc["machines"]["marchhare"]["workers"]
    assert nick in mw
    assert "31712" not in mw
    assert list(mw.keys()) == [nick]
    assert doc["workers"][nick]["state"] == "busy"


def test_two_present_workers_two_nick_entries_same_machine(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home, {"op": "worker_state", "nick": "marchhare-8592", "state": "busy", "job": "FR #159"}
    )
    apply_report(
        home, {"op": "worker_state", "nick": "marchhare-31712", "state": "busy", "job": "FR #162"}
    )
    snap = public_digest_snapshot(home)
    mw = snap["machines"]["marchhare"]["workers"]
    assert set(mw.keys()) == {"marchhare-8592", "marchhare-31712"}
    assert not any(k.isdigit() for k in mw)
    # grouped by machine — flamingo stays empty of these seats
    assert "marchhare-31712" not in (snap["machines"]["flamingo"].get("workers") or {})


def test_snapshot_prunes_stale_pid_ghosts(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home, {"op": "worker_state", "nick": "flamingo-43052", "state": "busy", "job": "x"}
    )
    doc = load_digest(home)
    # Simulate pre-FR#162 dual keys left on disk
    doc["machines"]["flamingo"]["workers"]["43052"] = {
        "pid": "43052",
        "nick": "flamingo-43052",
        "state": "busy",
        "working_on": "x",
    }
    save_digest(home, doc)
    snap = public_digest_snapshot(home)
    mw = snap["machines"]["flamingo"]["workers"]
    assert "flamingo-43052" in mw
    assert "43052" not in mw
    assert len(mw) == 1


def test_coerce_workers_promotes_ear_pid_to_nick():
    raw = {
        "43052": {
            "pid": "43052",
            "nick": "flamingo-43052",
            "state": "busy",
            "working_on": "job-a",
        }
    }
    out = coerce_workers("flamingo", raw)
    assert set(out.keys()) == {"flamingo-43052"}
    assert out["flamingo-43052"]["working_on"] == "job-a"
    assert "43052" not in out


def test_nick_keyed_drops_digit_when_nick_twin_exists():
    cleaned = nick_keyed_machine_workers(
        {
            "marchhare-1": {"state": "idle", "job": None},
            "1": {"pid": "1", "nick": "marchhare-1", "state": "idle"},
        }
    )
    assert set(cleaned.keys()) == {"marchhare-1"}


def test_prune_in_place_on_doc():
    doc = {
        "machines": {
            "ionos": {
                "workers": {
                    "ionos-9": {"state": "busy", "job": "z", "working_on": "z", "ts": "t"},
                    "9": {"pid": "9", "nick": "ionos-9", "state": "busy", "working_on": "z"},
                }
            }
        }
    }
    prune_machine_worker_ghosts(doc)
    assert set(doc["machines"]["ionos"]["workers"].keys()) == {"ionos-9"}
    assert doc["machines"]["ionos"]["working_on"] == "z"


def test_not_one_entry_per_machine_only(tmp_path: Path):
    """Digest must expose per-worker slots, not a single machine blob as the only worker view."""
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home, {"op": "worker_state", "nick": "ionos-11", "state": "busy", "job": "a"}
    )
    apply_report(
        home, {"op": "worker_state", "nick": "ionos-22", "state": "idle", "job": None}
    )
    snap = public_digest_snapshot(home)
    assert isinstance(snap["machines"]["ionos"]["workers"], dict)
    assert len(snap["machines"]["ionos"]["workers"]) == 2
    assert "ionos-11" in snap["workers"]
    assert "ionos-22" in snap["workers"]
