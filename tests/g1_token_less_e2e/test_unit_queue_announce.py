"""Unit coverage for queue supersede + announce formatting."""

from __future__ import annotations

from pathlib import Path

from jeeves.announce import format_github_webhook_announce, process_git_webhook
from jeeves.queue import Claim, apply_queue_event, claim_from_payload, load_queue


def test_issue_opened_is_fr_not_pr():
    claim = claim_from_payload(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 3, "title": "x"},
            "repository": {"full_name": "SimonBarnett/gh-Jeeves"},
        },
    )
    assert claim is not None
    assert claim.task == "FR"
    assert claim.id == "#3"


def test_supersede_fr_to_mrb(tmp_path: Path):
    home = tmp_path
    apply_queue_event(
        home,
        Claim(repo="a/b", task="FR", id="#1", line="feat"),
    )
    apply_queue_event(
        home,
        Claim(repo="a/b", task="MRB", id="#2", line="fix #1"),
    )
    q = load_queue(home)
    assert all(r["task"] != "FR" or r["id"] != "#1" for r in q["unaccepted"])
    assert any(r["task"] == "MRB" and r["id"] == "#2" for r in q["unaccepted"])


def test_announce_line_bounded():
    line = format_github_webhook_announce(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 1, "title": "t" * 500},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "u"},
        },
    )
    assert line is not None
    assert len(line.encode("utf-8")) <= 512  # FR #24 wire budget body+prefix; body alone soft
    assert line.startswith("GIT ") and ("issues" in line or "FR" in line)


def test_process_rejects_secret_field_only():
    line, claim, reject = process_git_webhook(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 1, "title": "mentions ghp_in_title_ok", "body": "x"},
            "repository": {"full_name": "o/r"},
        },
    )
    assert reject is None
    assert line is not None
    assert claim is not None

    line2, claim2, reject2 = process_git_webhook(
        "issues",
        {
            "action": "opened",
            "issue": {"number": 2, "title": "x", "body": "y"},
            "repository": {"full_name": "o/r"},
            "password": "ghp_secretvalue",
        },
    )
    assert reject2 is not None
    assert line2 is None
    assert claim2 is None
