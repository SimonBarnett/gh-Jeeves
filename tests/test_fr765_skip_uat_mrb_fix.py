"""bobiverse#765: do not enqueue UAT for mrb-*-fix / fix(mrb-N) PR merges.

Evidence: after MRB PASS on gh-Jeeves#229, fix PR #237 titled ``mrb-229-fix:…``
merged and Jeeves offered ``UAT …#237`` to the fix author (self-UAT trap).
#224/#227 already skipped MRB enqueue for those titles; merge→UAT did not.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.queue import apply_queue_event, claim_from_payload, load_queue, save_queue


REPO = "SimonBarnett/gh-Jeeves"


def test_merged_mrb_fix_pr_does_not_claim_uat():
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 237,
                "title": "mrb-229-fix: refuse cross-repo MRB pull URLs",
                "html_url": f"https://github.com/{REPO}/pull/237",
                "body": "Refs #229\nRefs SimonBarnett/bobiverse#247",
                "merged": True,
            },
        },
    )
    assert claim is None


def test_merged_mrb_fix_pr_does_not_enqueue_uat(tmp_path: Path):
    home = tmp_path / "h"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 237,
                "title": "fix(mrb-229): refuse cross-repo URLs",
                "html_url": f"https://github.com/{REPO}/pull/237",
                "body": "Closes #999\n\nAgent: marchhare-41928",
                "merged": True,
            },
        },
    )
    assert claim is None
    # Even if a caller forced apply with a hand-built claim, queue hygiene should
    # not leave a UAT for the fix PR id after a normal merge path (no claim).
    assert load_queue(home)["unaccepted"] == []


def test_merged_normal_pr_still_enqueues_uat(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 229,
                "title": "fix(bobiverse#247): never invent MRB /pull/N",
                "html_url": f"https://github.com/{REPO}/pull/229",
                "body": "Closes SimonBarnett/bobiverse#247\n\nAgent: marchhare-1",
                "merged": True,
            },
        },
    )
    assert claim is not None and claim.task == "UAT"
    apply_queue_event(home, claim)
    q = load_queue(home)
    uats = [r for r in q["unaccepted"] if str(r.get("task") or "").upper() == "UAT"]
    assert uats, q
    assert not any(str(r.get("id")) in ("#237", "237") for r in uats)


def test_assign_skips_stale_uat_row_with_mrb_fix_title(tmp_path: Path):
    """Already-queued UAT of a fix PR must not be offered (purge/skip)."""
    home = tmp_path / "h3"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "UAT",
                    "id": "#237",
                    "pr_id": "#237",
                    "line": "mrb-229-fix: refuse cross-repo MRB pull URLs",
                    "url": f"https://github.com/{REPO}/pull/237",
                    "merged": True,
                    "seq": 1,
                },
                {
                    "repo": "SimonBarnett/bobiverse",
                    "task": "FR",
                    "id": "#10",
                    "line": "next real job",
                    "url": "https://github.com/SimonBarnett/bobiverse/issues/10",
                    "seq": 2,
                },
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState(timeout_s=300)
    st.bind(home)
    d = st.decide(home, "marchhare-99", "#marchhare", live_nicks={"marchhare-99"})
    assert d.action == "assign"
    assert "FR" in (d.line or "")
    assert "#10" in (d.line or "")
    assert "UAT" not in (d.line or "")
    q = load_queue(home)
    assert not any(
        str(r.get("task") or "").upper() == "UAT" and str(r.get("id")) in ("#237", "237")
        for r in (q.get("unaccepted") or [])
    )
