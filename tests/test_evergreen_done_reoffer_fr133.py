"""FR #133: evergreen MRB-home not queued as FR; DONE mode mismatch; re-offer skip."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from jeeves.assign import ChairAssignState, row_key
from jeeves.queue import (
    Claim,
    accept_job,
    apply_queue_event,
    claim_from_payload,
    complete_job,
    is_evergreen_mrb_home,
    load_queue,
    save_queue,
)


def test_is_evergreen_mrb_home_title_and_label():
    assert is_evergreen_mrb_home("MRB: agentic_fomprep origin/main (Cursor/Grok handoff)", [])
    assert is_evergreen_mrb_home("something", [{"name": "mrb-home"}])
    assert is_evergreen_mrb_home("Hostile MRB home for HEAD", [{"name": "feature-request"}])
    assert not is_evergreen_mrb_home("FR: add DONE trailing words", [{"name": "bug"}])


def test_claim_from_payload_skips_evergreen_fr():
    payload = {
        "action": "opened",
        "repository": {"full_name": "SimonBarnett/agentic_fomprep"},
        "issue": {
            "number": 3,
            "title": "MRB: agentic_fomprep origin/main (Cursor/Grok handoff)",
            "body": "Hostile MRB home for HEAD",
            "html_url": "https://github.com/SimonBarnett/agentic_fomprep/issues/3",
            "labels": [{"name": "feature-request"}],
        },
    }
    assert claim_from_payload("issues", payload) is None
    # closed still yields CLOSE so boards can leave the queue
    payload["action"] = "closed"
    c = claim_from_payload("issues", payload)
    assert c is not None and c.task == "CLOSE"


def test_apply_queue_skips_evergreen_fr_line(tmp_path: Path):
    home = tmp_path / "e"
    home.mkdir()
    tag = apply_queue_event(
        home,
        Claim(
            repo="SimonBarnett/agentic_fomprep",
            task="FR",
            id="#3",
            line="MRB home for origin/main",
            url="https://github.com/SimonBarnett/agentic_fomprep/issues/3",
        ),
    )
    assert tag.startswith("skipped:FR:evergreen")
    assert load_queue(home)["unaccepted"] == []


def test_done_mode_mismatch_still_completes(tmp_path: Path, caplog):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "SimonBarnett/agentic_fomprep",
                    "task": "FR",
                    "id": "#3",
                    "nick": "marchhare-31712",
                    "line": "MRB home",
                }
            ],
            "done": [],
            "workers": {"marchhare-31712": {"state": "busy"}},
        },
    )
    with caplog.at_level(logging.WARNING, logger="jeeves.queue"):
        st, row = complete_job(
            home,
            "marchhare-31712",
            "MRB",  # DONE said MRB; accepted was FR
            "SimonBarnett/agentic_fomprep",
            "3",
            "FAIL",
            "https://github.com/SimonBarnett/agentic_fomprep/pull/54",
        )
    assert st == "done"
    assert row is not None
    assert any("done_mode_mismatch" in r.getMessage() for r in caplog.records)
    q = load_queue(home)
    assert q["accepted"] == []
    assert q["workers"]["marchhare-31712"]["state"] == "idle"


def test_reoffer_skip_after_n_timeouts(tmp_path: Path):
    home = tmp_path / "r"
    home.mkdir()
    row = {
        "repo": "SimonBarnett/agentic_fomprep",
        "task": "FR",
        "id": "#3",
        "line": "evergreen",
        "url": "https://github.com/SimonBarnett/agentic_fomprep/issues/3",
        "seq": 1,
    }
    other = {
        "repo": "SimonBarnett/gh-Jeeves",
        "task": "FR",
        "id": "#10",
        "line": "next",
        "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/10",
        "seq": 2,
    }
    save_queue(
        home,
        {"v": 1, "unaccepted": [row, other], "accepted": [], "done": [], "workers": {}},
    )
    st = ChairAssignState(timeout_s=1.0)
    st.bind(home)
    nick = "marchhare-31712"
    # Offer same job N times with timeout between (no ACK)
    for i in range(3):
        d = st.decide(home, nick, "#marchhare", now=1000.0 + i * 10)
        assert d.action == "assign"
        assert d.row and d.row["id"] == "#3"
        # expire open offer
        st.expire_timed_out(now=1000.0 + i * 10 + 2.0)
    # Fourth !bored should skip #3 and pick #10
    d4 = st.decide(home, nick, "#marchhare", now=2000.0)
    assert d4.action == "assign"
    assert d4.row is not None
    assert d4.row["id"] == "#10"
