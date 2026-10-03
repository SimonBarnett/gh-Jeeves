"""gh-Jeeves#230: port bobiverse#240 UAT author_seat + sibling block into assign."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import (
    ChairAssignState,
    mrb_blocked_for_author,
    review_blocked_for_author,
)
from jeeves.queue import (
    Claim,
    accept_job,
    apply_queue_event,
    complete_job,
    load_queue,
    save_queue,
)


REPO = "SimonBarnett/bobiverse"


def test_uat_author_blocked_when_other_seat_live():
    row = {
        "task": "UAT",
        "repo": REPO,
        "id": "#240",
        "author_seat": "marchhare-35600",
        "url": f"https://github.com/{REPO}/pull/240",
    }
    live = {"marchhare-35600", "flamingo-1"}
    assert review_blocked_for_author(row, "marchhare-35600", live)
    assert mrb_blocked_for_author(row, "marchhare-35600", live)  # alias
    assert not review_blocked_for_author(row, "flamingo-1", live)
    # sole live seat: not blocked (bobiverse#240 / #227 criteria)
    assert not review_blocked_for_author(row, "marchhare-35600", {"marchhare-35600"})


def test_uat_same_machine_sibling_blocked_when_other_machine_live():
    row = {
        "task": "UAT",
        "repo": REPO,
        "id": "#240",
        "author_seat": "marchhare-1",
        "implementer_seat": "marchhare-1",
    }
    live = {"marchhare-2", "flamingo-9"}
    assert review_blocked_for_author(row, "marchhare-2", live)
    assert not review_blocked_for_author(row, "flamingo-9", live)


def test_mrb_still_blocked_for_author_when_other_live():
    row = {
        "task": "MRB",
        "repo": REPO,
        "id": "#104",
        "author_seat": "marchhare-31712",
    }
    assert review_blocked_for_author(
        row, "marchhare-31712", {"marchhare-31712", "flamingo-1"}
    )
    assert not review_blocked_for_author(
        row, "marchhare-31712", {"marchhare-31712"}
    )


def test_decide_skips_uat_for_author_offers_other(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "UAT",
                    "id": "#240",
                    "seq": 1,
                    "ts": "t",
                    "line": "x",
                    "author_seat": "marchhare-35600",
                    "url": f"https://github.com/{REPO}/pull/240",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    live = {"marchhare-35600", "flamingo-1"}
    d1 = st.decide(home, "marchhare-35600", "#marchhare", live_nicks=live)
    assert d1.action == "nothing"
    d2 = st.decide(home, "flamingo-1", "#flamingo", live_nicks=live)
    assert d2.action == "assign"
    assert "UAT" in (d2.line or "") and "#240" in (d2.line or "")


def test_merged_pr_copies_author_seat_onto_uat(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#240",
                    "pr_id": "#240",
                    "nick": "ionos-2",
                    "author_seat": "marchhare-1",
                    "implementer_seat": "marchhare-1",
                    "refs": ["#227"],
                    "ts": "t",
                    "line": "x",
                }
            ],
            "done": [],
            "workers": {},
        },
    )
    claim = Claim(
        repo=REPO,
        task="UAT",
        id="#227",
        event="pull_request",
        action="merged",
        line="merged",
        url=f"https://github.com/{REPO}/pull/240",
        refs=("#227",),
        pr_id="#240",
        merged=True,
    )
    assert apply_queue_event(home, claim) == "enqueued:UAT"
    uats = [r for r in load_queue(home)["unaccepted"] if r.get("task") == "UAT"]
    assert len(uats) == 1
    assert uats[0].get("implementer_seat") == "marchhare-1"
    assert uats[0].get("mrb_author_seat") == "ionos-2"
    assert uats[0].get("author_seat") == "ionos-2"


def test_done_fr_stamps_author_seat_on_mrb(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": REPO,
                    "task": "FR",
                    "id": "#227",
                    "nick": "marchhare-35600",
                    "ts": "t",
                    "line": "x",
                    "channel": "#marchhare",
                }
            ],
            "done": [],
            "workers": {"marchhare-35600": {"state": "busy"}},
        },
    )
    st, job = complete_job(
        home,
        "marchhare-35600",
        "FR",
        REPO,
        "#227",
        result="PASS",
        url=f"https://github.com/{REPO}/pull/240",
    )
    assert st == "done"
    mrbs = [r for r in load_queue(home)["unaccepted"] if r.get("task") == "MRB"]
    assert len(mrbs) == 1
    assert mrbs[0].get("author_seat") == "marchhare-35600"
    assert mrbs[0].get("implementer_seat") == "marchhare-35600"
