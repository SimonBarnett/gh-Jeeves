"""K4 / FR #5: merged PRs must leave the queue (supersede); reopened FR requeues.

Evidence: skills-visionary #18/#19 etc. stayed as MRB after merge (ionos queue).
Port of agentic_irc #207 supersede rules into gh-Jeeves queue engine.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.queue import (
    Claim,
    apply_queue_event,
    claim_from_payload,
    load_queue,
    unaccepted_tasks,
)


def _ids(home: Path) -> set[tuple[str, str, str]]:
    return {
        (str(r.get("repo")), str(r.get("task")), str(r.get("id")))
        for r in unaccepted_tasks(home)
    }


def test_k4_merged_pr_drops_mrb_and_queues_uat(tmp_path: Path):
    """Gate: after merge, MRB for the PR must not stay queued."""
    home = tmp_path / "q"
    home.mkdir()
    # FR #19 open
    apply_queue_event(
        home,
        Claim("SimonBarnett/skills-visionary", "FR", "#19", "issues", "opened", "feat"),
    )
    # PR #18 opened closes #19 → MRB supersedes FR
    pr_open = claim_from_payload(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {
                "number": 18,
                "title": "gate",
                "body": "Closes #19",
                "html_url": "https://github.com/SimonBarnett/skills-visionary/pull/18",
            },
            "repository": {"full_name": "SimonBarnett/skills-visionary"},
        },
    )
    assert pr_open is not None
    assert pr_open.task == "MRB"
    assert pr_open.refs == ("#19",)
    apply_queue_event(home, pr_open)
    ids = _ids(home)
    assert ("SimonBarnett/skills-visionary", "MRB", "#18") in ids
    assert ("SimonBarnett/skills-visionary", "FR", "#19") not in ids

    # merge PR #18
    pr_merged = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {
                "number": 18,
                "merged": True,
                "title": "gate",
                "body": "Closes #19",
                "html_url": "https://github.com/SimonBarnett/skills-visionary/pull/18",
            },
            "repository": {"full_name": "SimonBarnett/skills-visionary"},
        },
    )
    assert pr_merged is not None and pr_merged.task == "UAT" and pr_merged.merged is True
    apply_queue_event(home, pr_merged)
    ids = _ids(home)
    # K4 FAIL mode was: MRB #18 still present
    assert ("SimonBarnett/skills-visionary", "MRB", "#18") not in ids, (
        "K4 FAIL: merged PR still queued as MRB"
    )
    assert ("SimonBarnett/skills-visionary", "UAT", "#19") in ids
    assert ("SimonBarnett/skills-visionary", "FR", "#19") not in ids


def test_k4_pr_supersedes_fr_idempotent(tmp_path: Path):
    home = tmp_path / "q2"
    home.mkdir()
    apply_queue_event(
        home,
        Claim("SimonBarnett/AgentMonitor", "FR", "#88", line="watch"),
    )
    pr = claim_from_payload(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {"number": 87, "title": "fix", "body": "Fixes #88"},
            "repository": {"full_name": "SimonBarnett/AgentMonitor"},
        },
    )
    assert pr is not None
    assert apply_queue_event(home, pr) == "enqueued:MRB"
    assert apply_queue_event(home, pr) == "enqueued:MRB"  # idempotent replace
    rows = [r for r in unaccepted_tasks(home) if r["id"] == "#87"]
    assert len(rows) == 1
    assert all(not (r["task"] == "FR" and r["id"] == "#88") for r in unaccepted_tasks(home))


def test_k4_pr_closed_unmerged_restores_fr(tmp_path: Path):
    home = tmp_path / "q3"
    home.mkdir()
    pr = claim_from_payload(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {"number": 203, "title": "x", "body": "Fixes #204"},
            "repository": {"full_name": "SimonBarnett/agentic_irc"},
        },
    )
    apply_queue_event(home, pr)
    closed = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {
                "number": 203,
                "merged": False,
                "title": "x",
                "body": "Fixes #204",
            },
            "repository": {"full_name": "SimonBarnett/agentic_irc"},
        },
    )
    assert closed is not None and closed.merged is False
    apply_queue_event(home, closed)
    ids = _ids(home)
    assert ("SimonBarnett/agentic_irc", "MRB", "#203") not in ids
    assert ("SimonBarnett/agentic_irc", "FR", "#204") in ids


def test_k4_issue_reopened_queues_fr(tmp_path: Path):
    home = tmp_path / "q4"
    home.mkdir()
    # closed removes
    apply_queue_event(
        home,
        Claim("SimonBarnett/agentic_build", "FR", "#327", "issues", "opened", "ergo"),
    )
    apply_queue_event(
        home,
        Claim("SimonBarnett/agentic_build", "CLOSE", "#327", "issues", "closed", "done"),
    )
    assert ("SimonBarnett/agentic_build", "FR", "#327") not in _ids(home)
    reopened = claim_from_payload(
        "issues",
        {
            "action": "reopened",
            "issue": {"number": 327, "title": "ergo"},
            "repository": {"full_name": "SimonBarnett/agentic_build"},
        },
    )
    assert reopened is not None and reopened.task == "FR"
    apply_queue_event(home, reopened)
    assert ("SimonBarnett/agentic_build", "FR", "#327") in _ids(home)


def test_k4_merged_without_closes_still_drops_mrb(tmp_path: Path):
    """Even without Closes #n, merged PR must remove its MRB row (K4)."""
    home = tmp_path / "q5"
    home.mkdir()
    apply_queue_event(
        home,
        Claim(
            "SimonBarnett/agentic_build",
            "MRB",
            "#326",
            "pull_request",
            "opened",
            "cleanup",
            pr_id="#326",
        ),
    )
    merged = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {
                "number": 326,
                "merged": True,
                "title": "cleanup",
                "body": "no linked issue",
            },
            "repository": {"full_name": "SimonBarnett/agentic_build"},
        },
    )
    apply_queue_event(home, merged)
    ids = _ids(home)
    assert ("SimonBarnett/agentic_build", "MRB", "#326") not in ids
    # UAT falls back to PR id when no Closes
    assert ("SimonBarnett/agentic_build", "UAT", "#326") in ids


def test_k4_extract_closes_refs_grammar():
    from jeeves.queue import extract_closes_issue_ids

    assert extract_closes_issue_ids("Closes #19", "Fixes #20 and Refs #21") == (
        "#19",
        "#20",
        "#21",
    )
