"""FR #13 / K12: busy on webhook/TipForm from ACK/DONE (seat may look idle).

Seed outcome: Busy state on the webhook/TipForm from ACK/DONE; the worker
pack echoes its task in the seat. Hidden agent -p runs must not be the
operator's only busy signal — TipForm reads machines.<id>.working_on.

Failing-test-first: encode TipForm-visible working_on before the mirror sets it.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.digest import apply_report, load_digest, public_digest_snapshot
from jeeves.queue import Claim, accept_job, apply_queue_event, complete_job, save_queue


def _seed_fr(home: Path, repo: str = "SimonBarnett/gh-Jeeves", n: int = 13) -> None:
    apply_queue_event(
        home,
        Claim(repo, "FR", f"#{n}", "issues", "opened", f"FR {repo}#{n}"),
    )


def test_ack_busy_sets_machine_working_on_for_tipform(tmp_path: Path):
    """TipForm (Get-BobTrayHover) reads machines.<id>.working_on for START tiles."""
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-31712"
    job = "SimonBarnett/gh-Jeeves FR #13"
    out = apply_report(
        home, {"op": "worker_state", "nick": nick, "state": "busy", "job": job}
    )
    assert out.ok
    doc = load_digest(home)
    m = doc["machines"]["marchhare"]
    assert m["working_on"] == job
    # pid alias TipForm/ear shape
    assert m["workers"]["31712"]["working_on"] == job
    assert m["workers"]["31712"]["state"] == "busy"
    # nick key also exposes working_on (not job-only)
    assert m["workers"][nick].get("working_on") == job or m["workers"][nick].get("job") == job
    snap = public_digest_snapshot(home)
    assert snap["machines"]["marchhare"]["working_on"] == job


def test_done_idle_clears_machine_working_on(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "flamingo-43052"
    job = "o/r FR #1"
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "busy", "job": job})
    assert load_digest(home)["machines"]["flamingo"]["working_on"] == job
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "idle", "job": None})
    m = load_digest(home)["machines"]["flamingo"]
    assert m["working_on"] in ("", None)
    assert m["workers"][nick]["state"] == "idle"
    assert m["workers"]["43052"].get("working_on") in ("", None)


def test_queue_accept_then_done_tipform_path(tmp_path: Path):
    """ACK accept + DONE via report ops fill then clear TipForm working_on."""
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-99"
    job = "SimonBarnett/gh-Jeeves FR #13"
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "state": "busy",
            "job": job,
            "accepted_row": {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "FR",
                "id": "#13",
            },
        },
    )
    assert load_digest(home)["machines"]["marchhare"]["working_on"] == job
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    assert load_digest(home)["machines"]["marchhare"]["working_on"] in ("", None)


def test_shop_ack_done_updates_digest_working_on(tmp_path: Path):
    """End-to-end queue ACK/DONE (what the chair records) → TipForm fields."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_fr(home)
    nick = "marchhare-31712"
    st, _row = accept_job(home, nick, "#marchhare", "FR", "SimonBarnett/gh-Jeeves", 13)
    assert st == "accepted"
    job = "SimonBarnett/gh-Jeeves FR #13"
    apply_report(
        home,
        {
            "op": "queue_accept",
            "nick": nick,
            "state": "busy",
            "job": job,
            "accepted_row": {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "FR",
                "id": "#13",
            },
        },
    )
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "busy", "job": job})
    assert public_digest_snapshot(home)["machines"]["marchhare"]["working_on"] == job
    complete_job(home, nick, "FR", "SimonBarnett/gh-Jeeves", 13, "PASS", "https://example/pr")
    apply_report(home, {"op": "queue_done", "nick": nick, "state": "idle"})
    apply_report(home, {"op": "worker_state", "nick": nick, "state": "idle"})
    assert public_digest_snapshot(home)["machines"]["marchhare"]["working_on"] in ("", None)


def test_second_busy_worker_keeps_machine_working_on(tmp_path: Path):
    """Idling one seat must not wipe working_on while a sibling stays busy."""
    home = tmp_path / "d"
    home.mkdir()
    apply_report(
        home,
        {"op": "worker_state", "nick": "marchhare-1", "state": "busy", "job": "job-a"},
    )
    apply_report(
        home,
        {"op": "worker_state", "nick": "marchhare-2", "state": "busy", "job": "job-b"},
    )
    apply_report(home, {"op": "worker_state", "nick": "marchhare-1", "state": "idle"})
    m = load_digest(home)["machines"]["marchhare"]
    assert m["working_on"] == "job-b"
    assert m["workers"]["marchhare-2"]["state"] == "busy"


def test_jeeves_worker_state_skill_documents_k12_echo():
    """Worker pack must echo the task in the seat (skill contract)."""
    root = Path(__file__).resolve().parents[1]
    skill = (root / "skills" / "jeeves-worker-state" / "SKILL.md").read_text(encoding="utf-8")
    assert "working_on" in skill or "TipForm" in skill
    assert "echo" in skill.lower() or "seat" in skill.lower()
    # K12: busy from ACK/DONE, not from TUI appearance
    assert "ACK" in skill and "DONE" in skill
    assert "hidden" in skill.lower() or "idle while" in skill.lower() or "console" in skill.lower()
