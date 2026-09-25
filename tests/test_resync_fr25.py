"""FR #25: rebuild task list from GitHub — acceptance tests (fake API)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jeeves.queue import Claim, apply_queue_event, load_queue, save_queue
from jeeves.resync import (
    DiffStats,
    FakeGitHub,
    ResyncConfig,
    ResyncScheduler,
    apply_webhook_during_resync,
    build_outstanding,
    reconcile_queue,
    run_resync,
)


def _issue(n: int, title: str, created: str = "2024-01-01T00:00:00Z") -> dict:
    return {
        "number": n,
        "title": title,
        "html_url": f"https://github.com/o/r/issues/{n}",
        "created_at": created,
        "state": "open",
    }


def _pr(
    n: int,
    title: str,
    body: str = "",
    *,
    merged: bool = False,
    state: str = "open",
    created: str = "2024-01-02T00:00:00Z",
    uat_stamped: bool = False,
) -> dict:
    return {
        "number": n,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/o/r/pull/{n}",
        "created_at": created,
        "merged": merged,
        "state": state,
        "uat_stamped": uat_stamped,
        "merged_at": "2024-01-03T00:00:00Z" if merged else None,
    }


@pytest.fixture
def home(tmp_path: Path) -> Path:
    d = tmp_path / "digest"
    d.mkdir()
    return d


def test_1_empty_queue_fixture_2fr_1mrb_1uat(home: Path):
    """Empty queue + 3 open issues, 1 open PR, 1 merged awaiting UAT → 2 FR + 1 MRB + 1 UAT."""
    gh = FakeGitHub(
        repos=["o/r"],
        issues={
            "o/r": [
                _issue(1, "A", "2024-01-01T00:00:00Z"),
                _issue(2, "B", "2024-01-01T01:00:00Z"),
                _issue(3, "C has PR", "2024-01-01T02:00:00Z"),
            ]
        },
        pulls={
            "o/r": [
                _pr(10, "impl", "closes #3", created="2024-01-02T00:00:00Z"),
            ]
        },
        closed_pulls={
            "o/r": [
                _pr(20, "done", "closes #99", merged=True, state="closed", uat_stamped=False),
            ]
        },
    )
    desired = build_outstanding(gh)
    tasks = sorted((r["task"], r["id"]) for r in desired)
    assert ("FR", "#1") in tasks
    assert ("FR", "#2") in tasks
    assert ("FR", "#3") not in tasks  # superseded by MRB
    assert ("MRB", "#10") in tasks
    assert ("UAT", "#99") in tasks
    assert len([t for t in tasks if t[0] == "FR"]) == 2
    assert len([t for t in tasks if t[0] == "MRB"]) == 1
    assert len([t for t in tasks if t[0] == "UAT"]) == 1
    # oldest first: #1 before #2
    frs = [r for r in desired if r["task"] == "FR"]
    assert frs[0]["id"] == "#1"
    assert frs[1]["id"] == "#2"

    stats = run_resync(home, gh)
    assert not stats.skipped_github_down
    q = load_queue(home)
    assert len(q["unaccepted"]) == 4
    assert q["unaccepted"][0]["id"] == "#1"


def test_2_stale_merged_and_closed_removed(home: Path):
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {"repo": "o/r", "task": "MRB", "id": "#50", "seq": 1, "line": "merged already"},
                {"repo": "o/r", "task": "FR", "id": "#8", "seq": 2, "line": "closed issue"},
                {"repo": "o/r", "task": "FR", "id": "#1", "seq": 3, "line": "still open"},
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    gh = FakeGitHub(
        repos=["o/r"],
        issues={"o/r": [_issue(1, "still open")]},
        pulls={"o/r": []},
        closed_pulls={"o/r": []},
    )
    stats = run_resync(home, gh)
    q = load_queue(home)
    keys = {(r["task"], r["id"]) for r in q["unaccepted"]}
    assert keys == {("FR", "#1")}
    assert stats.removed >= 2


def test_3_accepted_kept_if_connected_else_released(home: Path):
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [
                {
                    "repo": "o/r",
                    "task": "FR",
                    "id": "#1",
                    "seq": 1,
                    "nick": "flamingo-1",
                    "channel": "#flamingo",
                },
                {
                    "repo": "o/r",
                    "task": "FR",
                    "id": "#2",
                    "seq": 2,
                    "nick": "flamingo-gone",
                    "channel": "#flamingo",
                },
            ],
            "done": [],
            "workers": {
                "flamingo-1": {"state": "busy"},
                "flamingo-gone": {"state": "busy"},
            },
        },
    )
    gh = FakeGitHub(
        repos=["o/r"],
        issues={"o/r": [_issue(1, "a"), _issue(2, "b")]},
        pulls={"o/r": []},
        closed_pulls={"o/r": []},
    )
    stats = run_resync(home, gh, connected_nicks={"flamingo-1"})
    q = load_queue(home)
    acc_ids = {r["id"] for r in q["accepted"]}
    un_ids = {r["id"] for r in q["unaccepted"]}
    assert "#1" in acc_ids
    assert "#2" not in acc_ids
    assert "#2" in un_ids
    assert stats.accepted_kept == 1
    assert stats.accepted_released == 1


def test_4_github_down_keeps_queue(home: Path):
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [{"repo": "o/r", "task": "FR", "id": "#7", "seq": 1, "line": "keep"}],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    gh = FakeGitHub(repos=["o/r"], down=True)
    stats = run_resync(home, gh)
    assert stats.skipped_github_down
    q = load_queue(home)
    assert len(q["unaccepted"]) == 1
    assert q["unaccepted"][0]["id"] == "#7"

    # retry when back
    gh.down = False
    gh.issues = {"o/r": [_issue(7, "keep"), _issue(8, "new")]}
    gh.pulls = {"o/r": []}
    gh.closed_pulls = {"o/r": []}
    stats2 = run_resync(home, gh)
    assert not stats2.skipped_github_down
    q2 = load_queue(home)
    assert {r["id"] for r in q2["unaccepted"]} == {"#7", "#8"}


def test_5_webhook_during_rebuild_idempotent(home: Path):
    gh = FakeGitHub(
        repos=["o/r"],
        issues={"o/r": [_issue(1, "a")]},
        pulls={"o/r": []},
        closed_pulls={"o/r": []},
    )
    run_resync(home, gh)
    # live webhook same FR
    apply_webhook_during_resync(
        home,
        Claim(repo="o/r", task="FR", id="#1", line="a", event="issues", action="opened"),
    )
    q = load_queue(home)
    assert len([r for r in q["unaccepted"] if r["id"] == "#1" and r["task"] == "FR"]) == 1
    # second resync no duplicate
    run_resync(home, gh)
    q2 = load_queue(home)
    assert len([r for r in q2["unaccepted"] if r["id"] == "#1"]) == 1


def test_6_second_run_quiet_unchanged(home: Path):
    gh = FakeGitHub(
        repos=["o/r"],
        issues={"o/r": [_issue(1, "a"), _issue(2, "b")]},
        pulls={"o/r": []},
        closed_pulls={"o/r": []},
    )
    lines: list[str] = []
    s1 = run_resync(home, gh, outbox_append=lines.append, quiet_when_unchanged=True)
    assert not s1.quiet
    assert lines, "first run announces"
    lines.clear()
    s2 = run_resync(home, gh, outbox_append=lines.append, quiet_when_unchanged=True)
    assert s2.quiet
    assert lines == [], "second run silent when unchanged"


def test_7_summary_line_length_safe():
    st = DiffStats(added=2, removed=1, retyped=0)
    line = st.summary_line(total=10)
    assert "Jeeves resync:" in line
    assert "+2" in line
    assert "-1" in line
    assert "total 10" in line
    assert len(line.encode("utf-8")) < 400


def test_scheduler_start_and_on_demand(home: Path):
    gh = FakeGitHub(
        repos=["o/r"],
        issues={"o/r": [_issue(1, "a")]},
        pulls={"o/r": []},
        closed_pulls={"o/r": []},
    )
    cfg = ResyncConfig(interval_s=3600)
    sch = ResyncScheduler(home, gh, cfg=cfg)
    sch.start(run_immediately=True)
    try:
        assert sch.runs >= 1
        q = load_queue(home)
        assert q["unaccepted"]
        sch.run_once()  # on-demand
        assert sch.runs >= 2
    finally:
        sch.stop()


def test_allow_deny_repo_filter():
    gh = FakeGitHub(
        repos=["o/keep", "o/drop"],
        issues={
            "o/keep": [_issue(1, "k")],
            "o/drop": [_issue(2, "d")],
        },
        pulls={"o/keep": [], "o/drop": []},
        closed_pulls={"o/keep": [], "o/drop": []},
    )
    rows = build_outstanding(gh, ResyncConfig(deny_repos=["o/drop"]))
    assert all(r["repo"] == "o/keep" for r in rows)
