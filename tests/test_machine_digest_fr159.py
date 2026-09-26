"""FR #159: per-machine webhook/digest updates for all fleet seats.

Ear ``op=merge`` must not wipe ACK/DONE seat state under machines.<id>, and
aliases such as ``dev1`` must land on ``ce-priority-dev1``.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import (
    FLEET_MACHINE_IDS,
    apply_report,
    empty_digest,
    load_digest,
    normalize_machine_id,
    public_digest_snapshot,
)

# Canonical fleet seats named in the issue.
FLEET_FOUR = ("flamingo", "marchhare", "ionos", "ce-priority-dev1")


def _busy_all(home: Path) -> dict[str, str]:
    """ACK-shaped busy seats on every fleet machine; return mid -> nick."""
    nicks = {
        "flamingo": "flamingo-101",
        "marchhare": "marchhare-202",
        "ionos": "ionos-303",
        "ce-priority-dev1": "ce-priority-dev1-404",
    }
    assert tuple(nicks) == FLEET_FOUR
    for mid, nick in nicks.items():
        job = f"SimonBarnett/gh-Jeeves FR #{mid}"
        out = apply_report(
            home,
            {
                "op": "queue_accept",
                "nick": nick,
                "state": "busy",
                "job": job,
                "accepted_row": {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#159",
                },
            },
        )
        assert out.ok, mid
    return nicks


def test_fleet_machine_ids_match_issue():
    assert FLEET_MACHINE_IDS == FLEET_FOUR
    assert set(empty_digest()["machines"]) >= set(FLEET_FOUR)


def test_normalize_aliases_to_canonical():
    assert normalize_machine_id("flamingo") == "flamingo"
    assert normalize_machine_id("MARCHHARE") == "marchhare"
    assert normalize_machine_id("ionos") == "ionos"
    assert normalize_machine_id("ce-priority-dev1") == "ce-priority-dev1"
    assert normalize_machine_id("dev1") == "ce-priority-dev1"
    assert normalize_machine_id("bob-dev1") == "ce-priority-dev1"
    assert normalize_machine_id("bob-flamingo") == "flamingo"
    assert normalize_machine_id("bob-marchhare") == "marchhare"
    assert normalize_machine_id("bob-ionos") == "ionos"
    assert normalize_machine_id("bob-ce-priority-dev1") == "ce-priority-dev1"


def test_merge_empty_workers_preserves_busy_seats_all_four(tmp_path: Path):
    """Ear heartbeat with workers:{} must not blank machines.<id>.workers."""
    home = tmp_path / "d"
    home.mkdir()
    nicks = _busy_all(home)

    for mid in FLEET_FOUR:
        out = apply_report(
            home,
            {
                "op": "merge",
                "machine": mid,
                "online": True,
                "status": "operational",
                "workers": {},
                "working_on": "",
                "pcent": {"cursor-models": 12},
            },
        )
        assert out.ok, mid

    doc = load_digest(home)
    for mid, nick in nicks.items():
        mw = (doc["machines"][mid].get("workers") or {})
        assert nick in mw, mid
        assert mw[nick]["state"] == "busy", mid
        assert doc["machines"][mid]["working_on"], mid
        assert doc["machines"][mid]["online"] is True
        assert doc["machines"][mid]["pcent"].get("cursor-models") == 12


def test_merge_alias_dev1_updates_ce_priority(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    out = apply_report(
        home,
        {
            "op": "merge",
            "machine": "dev1",
            "online": True,
            "status": "operational",
            "pcent": {"cursor-models": 44, "grok-chat": 3},
            "running": 1,
        },
    )
    assert out.ok
    doc = load_digest(home)
    ent = doc["machines"]["ce-priority-dev1"]
    assert ent["online"] is True
    assert ent["pcent"]["cursor-models"] == 44
    assert ent["running"] == 1
    # Must not create an orphan machines.dev1 seat for the alias.
    assert "dev1" not in doc["machines"]


def test_merge_bob_prefixed_aliases_all_four(tmp_path: Path):
    """Ears often report nick bob-<machine>; updates must hit canonical keys."""
    home = tmp_path / "d"
    home.mkdir()
    aliases = {
        "bob-flamingo": "flamingo",
        "bob-marchhare": "marchhare",
        "bob-ionos": "ionos",
        "bob-ce-priority-dev1": "ce-priority-dev1",
    }
    for alias, mid in aliases.items():
        out = apply_report(
            home,
            {
                "op": "merge",
                "machine": alias,
                "online": True,
                "status": "operational",
                "pcent": {"cursor-models": 7},
            },
        )
        assert out.ok, alias
        doc = load_digest(home)
        assert doc["machines"][mid]["online"] is True, mid
        assert doc["machines"][mid]["pcent"]["cursor-models"] == 7
        assert alias not in doc["machines"]
        assert mid in doc["machines"]


def test_report_payload_get_shape_all_four(tmp_path: Path):
    """GET /bob/v1/report public snapshot carries all four machine seats."""
    home = tmp_path / "d"
    home.mkdir()
    for mid in FLEET_FOUR:
        apply_report(
            home,
            {
                "op": "merge",
                "machine": mid,
                "online": True,
                "status": "operational",
                "lastSeen": "2026-09-26T12:00:00Z",
                "pcent": {"cursor-models": 1},
                "running": 0,
                "queued": 0,
            },
        )
    nicks = _busy_all(home)
    snap = public_digest_snapshot(home)
    assert set(FLEET_FOUR).issubset(set(snap["machines"]))
    for mid, nick in nicks.items():
        ent = snap["machines"][mid]
        for key in (
            "id",
            "nick",
            "shop",
            "online",
            "status",
            "working_on",
            "workers",
            "lastSeen",
            "pcent",
            "running",
            "queued",
        ):
            assert key in ent, (mid, key)
        assert ent["id"] == mid
        assert ent["shop"] == f"#{mid}"
        assert nick in (ent.get("workers") or {})
        assert ent["working_on"]
        assert snap["workers"][nick]["state"] == "busy"


def test_merge_pid_worker_still_updates(tmp_path: Path):
    """Non-destructive merge still applies ear pid-keyed worker rows."""
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home,
        {
            "op": "merge",
            "machine": "flamingo",
            "online": True,
            "workers": {
                "46804": {
                    "nick": "flamingo-46804",
                    "state": "busy",
                    "working_on": "FR ear",
                }
            },
        },
    )
    fl = load_digest(home)["machines"]["flamingo"]["workers"]
    assert "46804" in fl
    assert fl["46804"]["working_on"] == "FR ear"
