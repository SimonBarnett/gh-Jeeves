"""bobiverse#224: skip MRB for mrb-*-fix titles and already-merged PRs.

Evidence: after MRB FAIL on agentic_fomprep#105, seat opened/merged fix #106
(title fix(mrb-105):...), then Jeeves still assigned MRB #106 to the same seat
while the PR was already MERGED.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState, mrb_row_not_offerable, purge_stale_mrb_rows
from jeeves.queue import (
    Claim,
    apply_queue_event,
    claim_from_payload,
    is_mrb_fix_pr_title,
    load_queue,
    save_queue,
)


REPO = "SimonBarnett/agentic_fomprep"


def test_is_mrb_fix_pr_title_matches_common_shapes():
    assert is_mrb_fix_pr_title("fix(mrb-105): CAT-T57 ASCII gate")
    assert is_mrb_fix_pr_title("fix(mrb_105): stuff")
    assert is_mrb_fix_pr_title("mrb-105-fix: restore gate")
    assert is_mrb_fix_pr_title("MRB-12-FIX duplicate ids")
    assert not is_mrb_fix_pr_title("fix(fr-104): ASCII-only comments")
    assert not is_mrb_fix_pr_title("docs(mrb-105): note")
    assert not is_mrb_fix_pr_title("feat: something else")


def test_pr_opened_mrb_fix_title_does_not_enqueue(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "opened",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 106,
                "title": "fix(mrb-105): CAT-T57 ASCII gate + CAT-T58 unique ids",
                "html_url": f"https://github.com/{REPO}/pull/106",
                "body": "MRB fix for #105\n\nAgent: marchhare-16564",
                "merged": False,
            },
        },
    )
    assert claim is None
    # apply would no-op; queue stays empty
    assert load_queue(home)["unaccepted"] == []


def test_pr_opened_normal_title_still_enqueues_mrb(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "opened",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 200,
                "title": "fix(fr-199): real feature PR",
                "html_url": f"https://github.com/{REPO}/pull/200",
                "body": "Fixes #199\n\nAgent: marchhare-41912",
                "merged": False,
            },
        },
    )
    assert claim is not None and claim.task == "MRB"
    assert claim.author_seat == "marchhare-41912"
    apply_queue_event(home, claim)
    q = load_queue(home)
    mrbs = [r for r in q["unaccepted"] if r.get("task") == "MRB"]
    assert len(mrbs) == 1
    assert mrbs[0].get("author_seat") == "marchhare-41912"


def test_assign_skips_and_purges_merged_mrb_row(tmp_path: Path):
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
                    "id": "#106",
                    "line": "fix(mrb-105): already merged",
                    "url": f"https://github.com/{REPO}/pull/106",
                    "merged": True,
                    "seq": 1,
                },
                {
                    "repo": REPO,
                    "task": "FR",
                    "id": "#110",
                    "line": "next real job",
                    "url": f"https://github.com/{REPO}/issues/110",
                    "seq": 2,
                },
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "marchhare-41912", "#marchhare", live_nicks={"marchhare-41912"})
    assert d.action == "assign"
    assert "FR" in (d.line or "")
    assert "#110" in (d.line or "")
    q = load_queue(home)
    assert not any(
        str(r.get("task") or "").upper() == "MRB" and str(r.get("id")) in ("#106", "106")
        for r in (q.get("unaccepted") or [])
    )


def test_assign_skips_stale_mrb_fix_title_even_without_merged_flag(tmp_path: Path):
    home = tmp_path / "h4"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#106",
                    "line": "fix(mrb-105): CAT-T57 ASCII gate",
                    "url": f"https://github.com/{REPO}/pull/106",
                    "seq": 1,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    assert mrb_row_not_offerable(
        {
            "task": "MRB",
            "id": "#106",
            "line": "fix(mrb-105): CAT-T57 ASCII gate",
        }
    )
    n = purge_stale_mrb_rows(home)
    assert n >= 1
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "marchhare-16564", "#marchhare", live_nicks={"marchhare-16564"})
    assert d.action == "nothing"
    assert "nothing queued" in (d.line or "")


def test_mrb_superseded_when_uat_for_same_pr_exists(tmp_path: Path):
    home = tmp_path / "h5"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "MRB",
                    "id": "#106",
                    "line": "some title",
                    "url": f"https://github.com/{REPO}/pull/106",
                    "seq": 1,
                },
                {
                    "repo": REPO,
                    "task": "UAT",
                    "id": "#0",
                    "repo_uat": True,
                    "merged_prs": ["#106"],
                    "merged": True,
                    "line": f"UAT {REPO}: all clear",
                    "seq": 2,
                },
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    n = purge_stale_mrb_rows(home)
    assert n >= 1
    q = load_queue(home)
    assert not any(str(r.get("task") or "").upper() == "MRB" for r in q["unaccepted"])
    assert any(
        str(r.get("task") or "").upper() == "UAT" and r.get("repo_uat")
        for r in q["unaccepted"]
    )
