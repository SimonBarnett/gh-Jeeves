"""bobiverse#740: gh-Jeeves must not re-offer already-DONE MRB rows."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.queue import load_queue, mrb_already_done, purge_dead_mrb_unaccepted, save_queue


REPO = "SimonBarnett/bobiverse"


def test_mrb_already_done_and_purge(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#656",
                    "seq": 1,
                    "ts": "t",
                    "line": "x",
                    "url": f"https://github.com/{REPO}/pull/656",
                }
            ],
            "accepted": [],
            "done": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#656",
                    "nick": "marchhare-41928",
                    "result": "PASS",
                }
            ],
            "workers": {},
        },
    )
    doc = load_queue(home)
    assert mrb_already_done(doc, doc["unaccepted"][0])
    assert purge_dead_mrb_unaccepted(home) == 1
    assert load_queue(home)["unaccepted"] == []


def test_decide_skips_done_mrb(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#658",
                    "seq": 1,
                    "ts": "t",
                    "line": "x",
                    "url": f"https://github.com/{REPO}/pull/658",
                }
            ],
            "accepted": [],
            "done": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#658",
                    "nick": "marchhare-41928",
                    "result": "PASS",
                }
            ],
            "workers": {"flamingo-9": {"state": "idle"}},
        },
    )
    st = ChairAssignState(timeout_s=90)
    d = st.decide(home, "flamingo-9", "#flamingo", live_nicks={"flamingo-9"})
    assert d.action == "nothing"
    assert load_queue(home)["unaccepted"] == []
