"""bobiverse#768: UAT is per REPO (``UAT owner/repo#0``), never per-PR.

After #754 restored t853u on bobiverse gitclaim, live gh-Jeeves still offered
``UAT …#229`` (single merged PR). Merge must not enqueue per-PR UAT; resync
enqueues one ``#0`` / ``repo_uat`` row only when the repo has no open issues
and no open PRs.
"""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.queue import Claim, apply_queue_event, claim_from_payload, load_queue, save_queue
from jeeves.resync import FakeGitHub, build_outstanding, run_resync


REPO = "SimonBarnett/gh-Jeeves"


def _issue(n: int, title: str, created: str = "2024-01-01T00:00:00Z") -> dict:
    return {
        "number": n,
        "title": title,
        "body": "",
        "html_url": f"https://github.com/{REPO}/issues/{n}",
        "created_at": created,
        "state": "open",
        "labels": [],
    }


def _pr(
    n: int,
    title: str,
    body: str = "",
    *,
    merged: bool = False,
    state: str = "open",
    uat_stamped: bool = False,
    created: str = "2024-01-02T00:00:00Z",
) -> dict:
    return {
        "number": n,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{REPO}/pull/{n}",
        "created_at": created,
        "merged_at": "2024-01-03T00:00:00Z" if merged else None,
        "merged": merged,
        "state": state,
        "uat_stamped": uat_stamped,
    }


def test_merged_pr_webhook_does_not_enqueue_per_pr_uat(tmp_path: Path):
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
                    "id": "#229",
                    "pr_id": "#229",
                    "line": "fix(bobiverse#247)",
                    "url": f"https://github.com/{REPO}/pull/229",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    claim = claim_from_payload(
        "pull_request",
        {
            "action": "closed",
            "repository": {"full_name": REPO},
            "pull_request": {
                "number": 229,
                "title": "fix(bobiverse#247): never invent MRB /pull/N",
                "html_url": f"https://github.com/{REPO}/pull/229",
                "body": "Closes SimonBarnett/bobiverse#247",
                "merged": True,
            },
        },
    )
    assert claim is not None
    # Cleanup claim may still be task=UAT for apply routing, but must not leave per-PR UAT.
    result = apply_queue_event(home, claim)
    assert result == "merged:no_per_pr_uat"
    q = load_queue(home)
    uats = [r for r in q["unaccepted"] if str(r.get("task") or "").upper() == "UAT"]
    assert uats == [], uats
    assert not any(str(r.get("task") or "").upper() == "MRB" for r in q["unaccepted"])


def test_build_outstanding_no_uat_while_open_work_exists():
    gh = FakeGitHub(
        repos=[REPO],
        issues={REPO: [_issue(1, "open FR")]},
        pulls={REPO: [_pr(10, "open MRB", "closes #2")]},
        closed_pulls={
            REPO: [_pr(229, "merged", "closes #247", merged=True, state="closed")]
        },
    )
    desired = build_outstanding(gh)
    tasks = [(r["task"], r["id"]) for r in desired]
    assert ("MRB", "#10") in tasks
    assert ("FR", "#1") in tasks
    assert not any(t[0] == "UAT" for t in tasks)


def test_build_outstanding_repo_uat_when_clear():
    gh = FakeGitHub(
        repos=[REPO],
        issues={REPO: []},
        pulls={REPO: []},
        closed_pulls={
            REPO: [
                _pr(
                    229,
                    "fix(bobiverse#247): never invent MRB /pull/N",
                    "Closes #247",
                    merged=True,
                    state="closed",
                )
            ]
        },
    )
    desired = build_outstanding(gh)
    uats = [r for r in desired if r.get("task") == "UAT"]
    assert len(uats) == 1
    assert uats[0]["id"] == "#0"
    assert uats[0].get("repo_uat") is True
    assert "229" in str(uats[0].get("merged_prs") or uats[0].get("line") or "")


def test_assign_skips_legacy_per_pr_uat(tmp_path: Path):
    home = tmp_path / "h2"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": REPO,
                    "task": "UAT",
                    "id": "#229",
                    "pr_id": "#229",
                    "line": "fix(bobiverse#247)",
                    "url": f"https://github.com/{REPO}/pull/229",
                    "seq": 1,
                },
                {
                    "repo": "SimonBarnett/bobiverse",
                    "task": "FR",
                    "id": "#10",
                    "line": "next",
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
    d = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d.action == "assign"
    assert "FR" in (d.line or "") and "#10" in (d.line or "")
    assert "UAT" not in (d.line or "")
    q = load_queue(home)
    assert not any(
        str(r.get("task") or "").upper() == "UAT" and not r.get("repo_uat")
        for r in (q.get("unaccepted") or [])
    )


def test_assign_offers_repo_level_uat(tmp_path: Path):
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
                    "id": "#0",
                    "repo_uat": True,
                    "line": f"UAT {REPO}: all issues closed, all PRs merged",
                    "url": f"https://github.com/{REPO}",
                    "merged_prs": ["#229"],
                    "seq": 1,
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = ChairAssignState(timeout_s=300)
    st.bind(home)
    d = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d.action == "assign"
    assert "UAT" in (d.line or "") and f"{REPO}#0" in (d.line or "")
