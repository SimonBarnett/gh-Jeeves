"""FR #161: ACK/DONE drive deterministic worker busy-idle digest task shape."""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import (
    apply_report,
    load_digest,
    public_digest_snapshot,
    worker_busy_entry,
    worker_idle_entry,
)
from jeeves.queue import Claim, accept_job, apply_queue_event, complete_job, load_queue


def test_worker_busy_entry_shape():
    ent = worker_busy_entry(
        repo="SimonBarnett/gh-Jeeves",
        kind="FR",
        ident="161",
        title="FR: ACK and DONE drive busy-idle",
    )
    assert ent["state"] == "busy"
    assert ent["repo"] == "SimonBarnett/gh-Jeeves"
    assert ent["kind"] == "FR"
    assert ent["id"] == "#161"
    assert ent["ref"] == "SimonBarnett/gh-Jeeves#161"
    assert ent["title"] == "FR: ACK and DONE drive busy-idle"
    assert "FR" in ent["job"] and "#161" in ent["job"]


def test_worker_idle_entry_clears_task_fields():
    ent = worker_idle_entry()
    assert ent["state"] == "idle"
    assert ent["job"] is None
    assert ent["repo"] is None
    assert ent["kind"] is None
    assert ent["id"] is None
    assert ent["ref"] is None
    assert ent["title"] is None
    assert ent["working_on"] == ""


def test_ack_sets_busy_task_shape_on_digest(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    title = "FR: ACK and DONE drive busy-idle"
    apply_queue_event(
        home,
        Claim(
            "SimonBarnett/gh-Jeeves",
            "FR",
            "#161",
            "issues",
            "opened",
            title,
        ),
    )
    nick = "marchhare-16101"
    st, row = accept_job(
        home, nick, "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "161"
    )
    assert st == "accepted"
    # Mirror via report path used by chair
    out = apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "state": "busy",
            "job": "SimonBarnett/gh-Jeeves FR #161",
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "FR",
            "id": "#161",
            "title": title,
            "accepted_row": row,
        },
    )
    assert out.ok
    doc = load_digest(home)
    top = doc["workers"][nick]
    assert top["state"] == "busy"
    assert top["repo"] == "SimonBarnett/gh-Jeeves"
    assert top["kind"] == "FR"
    assert top["ref"] == "SimonBarnett/gh-Jeeves#161"
    assert top["title"] == title
    mw = doc["machines"]["marchhare"]["workers"][nick]
    assert mw["state"] == "busy"
    assert mw["ref"] == "SimonBarnett/gh-Jeeves#161"
    assert mw["kind"] == "FR"
    assert mw["title"] == title


def test_done_returns_idle_clears_task(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "MRB", "#99", "issues", "opened", "MRB board"),
    )
    nick = "flamingo-9901"
    accept_job(home, nick, "#flamingo", "MRB", "SimonBarnett/gh-Jeeves", "99")
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "repo": "SimonBarnett/gh-Jeeves",
            "task": "MRB",
            "id": "#99",
            "title": "MRB board",
            "accepted_row": {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "MRB",
                "id": "#99",
                "line": "MRB board",
                "nick": nick,
            },
        },
    )
    assert load_digest(home)["workers"][nick]["state"] == "busy"
    complete_job(home, nick, "MRB", "SimonBarnett/gh-Jeeves", "99", "PASS", "https://example/pr/1")
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    doc = load_digest(home)
    idle = doc["workers"][nick]
    assert idle["state"] == "idle"
    assert idle.get("job") is None
    assert idle.get("repo") is None
    assert idle.get("kind") is None
    assert idle.get("title") is None
    assert idle.get("ref") is None
    mw = doc["machines"]["flamingo"]["workers"][nick]
    assert mw["state"] == "idle"
    assert mw.get("job") in (None, "")
    assert mw.get("title") in (None, "")


def test_idle_until_next_ack_no_stale_resurrect(tmp_path: Path):
    """After DONE, GET heal / ear-style merge must not resurrect the old task."""
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("o/r", "FR", "#1", "issues", "opened", "First"),
    )
    nick = "ionos-42"
    accept_job(home, nick, "#ionos", "FR", "o/r", "1")
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "repo": "o/r",
            "task": "FR",
            "id": "#1",
            "title": "First",
            "accepted_row": {"repo": "o/r", "task": "FR", "id": "#1", "line": "First"},
        },
    )
    complete_job(home, nick, "FR", "o/r", "1")
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    # Ear heartbeat with empty workers must not bring First back
    apply_report(
        home,
        {
            "op": "merge",
            "machine": "ionos",
            "online": True,
            "workers": {},
            "working_on": "First",
        },
    )
    snap = public_digest_snapshot(home)
    w = snap["workers"][nick]
    assert w["state"] == "idle"
    assert w.get("job") is None
    assert w.get("title") in (None, "")
    assert w.get("ref") in (None, "")
    # Next ACK sets a new task
    apply_queue_event(
        home,
        Claim("o/r", "FR", "#2", "issues", "opened", "Second"),
    )
    accept_job(home, nick, "#ionos", "FR", "o/r", "2")
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "repo": "o/r",
            "task": "FR",
            "id": "#2",
            "title": "Second",
            "accepted_row": {"repo": "o/r", "task": "FR", "id": "#2", "line": "Second"},
        },
    )
    busy = load_digest(home)["workers"][nick]
    assert busy["state"] == "busy"
    assert busy["ref"] == "o/r#2"
    assert busy["title"] == "Second"
    assert busy["kind"] == "FR"


def test_accept_job_queue_workers_include_title(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("SimonBarnett/gh-Jeeves", "FR", "#161", "issues", "opened", "Titled FR"),
    )
    accept_job(home, "marchhare-7", "#marchhare", "FR", "SimonBarnett/gh-Jeeves", "161")
    w = load_queue(home)["workers"]["marchhare-7"]
    assert w["state"] == "busy"
    assert w["kind"] == "FR"
    assert w["ref"] == "SimonBarnett/gh-Jeeves#161"
    assert w["title"] == "Titled FR"
