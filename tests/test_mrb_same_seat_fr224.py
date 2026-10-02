"""FR #224: never offer MRB of a PR to the seat that just DONE FR'd it."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState, mrb_blocked_for_author
from jeeves.queue import accept_job, complete_job, load_queue, save_queue


REPO = "SimonBarnett/gh-Jeeves"


def test_mrb_author_always_blocked_even_when_sole_live_seat():
    row = {
        "task": "MRB",
        "repo": REPO,
        "id": "#223",
        "author_seat": "marchhare-41912",
        "url": f"https://github.com/{REPO}/pull/223",
    }
    # FR #224: sole live seat that authored the PR must still be blocked.
    assert mrb_blocked_for_author(row, "marchhare-41912", {"marchhare-41912"})
    assert mrb_blocked_for_author(
        row, "marchhare-41912", {"marchhare-41912", "flamingo-1"}
    )
    assert not mrb_blocked_for_author(
        row, "flamingo-1", {"marchhare-41912", "flamingo-1"}
    )


def test_done_fr_stamps_author_seat_on_mrb_row(tmp_path: Path):
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
                    "id": "#222",
                    "nick": "marchhare-41912",
                    "channel": "#marchhare",
                    "line": "FR #222",
                    "url": f"https://github.com/{REPO}/issues/222",
                }
            ],
            "done": [],
            "workers": {"marchhare-41912": {"state": "busy"}},
        },
    )
    st, _row = complete_job(
        home,
        "marchhare-41912",
        "FR",
        REPO,
        "222",
        "ok",
        f"https://github.com/{REPO}/pull/223",
    )
    assert st == "done"
    q = load_queue(home)
    mrbs = [
        r
        for r in (q.get("unaccepted") or [])
        if str(r.get("task") or "").upper() == "MRB"
        and str(r.get("id") or "") in ("#223", "223")
    ]
    assert len(mrbs) == 1, q.get("unaccepted")
    assert mrbs[0].get("author_seat") == "marchhare-41912"


def test_assign_skips_author_when_only_seat(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#223",
                    "author_seat": "marchhare-41912",
                    "url": f"https://github.com/{REPO}/pull/223",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(
        home,
        "marchhare-41912",
        "#marchhare",
        live_nicks={"marchhare-41912"},
    )
    assert d.action == "nothing"
    assert "nothing queued" in (d.line or "")


def test_other_seat_still_gets_mrb(tmp_path: Path):
    home = tmp_path / "h3"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#223",
                    "author_seat": "marchhare-41912",
                    "url": f"https://github.com/{REPO}/pull/223",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(
        home,
        "flamingo-1",
        "#flamingo",
        live_nicks={"marchhare-41912", "flamingo-1"},
    )
    assert d.action == "assign"
    assert "MRB" in (d.line or "")
    assert "#223" in (d.line or "")
