"""FR #16 / K15: Closes #N must not drop FR when MRB FAILed.

Seed outcome: Queue treats an issue closed by a merge whose MRB failed as
still open (restore FR). skills-visionary #19 had to be reopened by hand.

Failing-test-first: encode DONE MRB FAIL → restore FR; later issues closed
(from Closes on the merged PR) must leave FR queued, not remove it; merged
PR after FAIL must not enqueue UAT.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.queue import (
    Claim,
    accept_job,
    apply_queue_event,
    claim_from_payload,
    complete_job,
    load_queue,
    unaccepted_tasks,
)


def _ids(home: Path) -> set[tuple[str, str, str]]:
    return {
        (r["repo"], r["task"], r["id"])
        for bucket in ("unaccepted", "accepted")
        for r in load_queue(home).get(bucket) or []
    }


def _seed_fr_mrb(home: Path, *, repo: str = "SimonBarnett/skills-visionary") -> None:
    apply_queue_event(
        home,
        Claim(repo, "FR", "#19", "issues", "opened", "feature", url=f"https://github.com/{repo}/issues/19"),
    )
    apply_queue_event(
        home,
        Claim(
            repo,
            "MRB",
            "#20",
            "pull_request",
            "opened",
            "impl Closes #19",
            refs=("#19",),
            pr_id="#20",
            url=f"https://github.com/{repo}/pull/20",
        ),
    )


def test_done_mrb_fail_restores_fr(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    repo = "SimonBarnett/skills-visionary"
    _seed_fr_mrb(home, repo=repo)
    assert ("repo" != "") or True
    assert (repo, "MRB", "#20") in _ids(home)
    assert (repo, "FR", "#19") not in _ids(home)

    st, _ = accept_job(home, "marchhare-1", "#marchhare", "MRB", repo, 20)
    assert st == "accepted"
    st2, row = complete_job(home, "marchhare-1", "MRB", repo, "20", "FAIL", "fix#21")
    assert st2 == "done"
    assert row is not None
    ids = _ids(home)
    assert (repo, "MRB", "#20") not in ids
    assert (repo, "FR", "#19") in ids


def test_issue_closed_after_mrb_fail_keeps_fr(tmp_path: Path):
    """K15: GitHub auto-close from Closes #N after FAIL must not wipe FR."""
    home = tmp_path / "d"
    home.mkdir()
    repo = "SimonBarnett/skills-visionary"
    _seed_fr_mrb(home, repo=repo)
    accept_job(home, "marchhare-1", "#marchhare", "MRB", repo, 20)
    complete_job(home, "marchhare-1", "MRB", repo, "20", "FAIL fix#21")
    assert (repo, "FR", "#19") in _ids(home)

    closed = claim_from_payload(
        "issues",
        {
            "action": "closed",
            "issue": {"number": 19, "title": "feature", "state": "closed"},
            "repository": {"full_name": repo},
        },
    )
    assert closed is not None and closed.task == "CLOSE"
    tag = apply_queue_event(home, closed)
    assert "restored" in tag or "held" in tag or "fail" in tag.lower() or tag.startswith("kept")
    assert (repo, "FR", "#19") in _ids(home)
    # no UAT for a failed MRB
    assert (repo, "UAT", "#19") not in _ids(home)


def test_merged_pr_after_mrb_fail_restores_fr_not_uat(tmp_path: Path):
    """Merged implementer/fix PR with Closes must not UAT after MRB FAIL."""
    home = tmp_path / "d"
    home.mkdir()
    repo = "SimonBarnett/skills-visionary"
    _seed_fr_mrb(home, repo=repo)
    accept_job(home, "marchhare-1", "#marchhare", "MRB", repo, 20)
    complete_job(home, "marchhare-1", "MRB", repo, "20", "FAIL")

    merged = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {
                "number": 20,
                "merged": True,
                "title": "impl",
                "body": "Closes #19",
            },
            "repository": {"full_name": repo},
        },
    )
    # Without K15 awareness this becomes UAT; with K15 it must restore FR.
    apply_queue_event(home, merged)
    ids = _ids(home)
    assert (repo, "UAT", "#19") not in ids
    assert (repo, "FR", "#19") in ids
    assert (repo, "MRB", "#20") not in ids


def test_normal_issue_closed_still_removes_fr(tmp_path: Path):
    """Without MRB FAIL hold, CLOSE still drops the FR."""
    home = tmp_path / "d"
    home.mkdir()
    repo = "o/r"
    apply_queue_event(home, Claim(repo, "FR", "#1", "issues", "opened", "x"))
    apply_queue_event(home, Claim(repo, "CLOSE", "#1", "issues", "closed", "done"))
    assert (repo, "FR", "#1") not in _ids(home)


def test_mrb_pass_merged_still_enqueues_uat(tmp_path: Path):
    """PASS path unchanged: merged PR with Closes → UAT."""
    home = tmp_path / "d"
    home.mkdir()
    repo = "o/r"
    apply_queue_event(home, Claim(repo, "FR", "#1", "issues", "opened", "x"))
    apply_queue_event(
        home,
        Claim(repo, "MRB", "#2", "pull_request", "opened", "Closes #1", refs=("#1",), pr_id="#2"),
    )
    accept_job(home, "m-1", "#m", "MRB", repo, 2)
    complete_job(home, "m-1", "MRB", repo, "2", "PASS", "merged")
    merged = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {
                "number": 2,
                "merged": True,
                "title": "ok",
                "body": "Closes #1",
            },
            "repository": {"full_name": repo},
        },
    )
    apply_queue_event(home, merged)
    ids = _ids(home)
    assert (repo, "UAT", "#1") in ids
    assert (repo, "FR", "#1") not in ids
