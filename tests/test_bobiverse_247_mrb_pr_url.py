"""bobiverse#247: never invent /pull/N from an issue id for MRB offers."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import (
    ChairAssignState,
    format_assign_line,
    mrb_row_offerable,
    resolve_assign_url,
)
from jeeves.queue import save_queue


def _queue(home: Path, rows: list[dict]) -> None:
    save_queue(
        home,
        {"v": 1, "unaccepted": rows, "accepted": [], "done": [], "workers": {}},
    )


def test_mrb_without_url_does_not_invent_pull_from_issue_id():
    """Regression: MRB #227 (issue) must not become .../pull/227."""
    line = format_assign_line(
        "marchhare-41912",
        {
            "task": "MRB",
            "repo": "SimonBarnett/bobiverse",
            "id": "#227",
            # no url, no pr_id — classic bad queue row
        },
    )
    assert "pull/227" not in line
    assert "marchhare-41912: MRB SimonBarnett/bobiverse#227" in line


def test_mrb_with_explicit_pr_id_may_build_pull_url():
    line = format_assign_line(
        "marchhare-1",
        {
            "task": "MRB",
            "repo": "SimonBarnett/bobiverse",
            "id": "#240",
            "pr_id": "#240",
        },
    )
    assert line.endswith("https://github.com/SimonBarnett/bobiverse/pull/240")


def test_mrb_keeps_real_pull_url():
    line = format_assign_line(
        "marchhare-1",
        {
            "task": "MRB",
            "repo": "SimonBarnett/bobiverse",
            "id": "#240",
            "url": "https://github.com/SimonBarnett/bobiverse/pull/240",
        },
    )
    assert "pull/240" in line


def test_mrb_issues_url_not_offerable():
    row = {
        "task": "MRB",
        "repo": "SimonBarnett/bobiverse",
        "id": "#227",
        "url": "https://github.com/SimonBarnett/bobiverse/issues/227",
    }
    assert mrb_row_offerable(row) is False


def test_mrb_missing_pull_url_not_offerable():
    row = {"task": "MRB", "repo": "SimonBarnett/bobiverse", "id": "#227"}
    assert mrb_row_offerable(row) is False


def test_mrb_real_pull_offerable():
    row = {
        "task": "MRB",
        "repo": "SimonBarnett/bobiverse",
        "id": "#240",
        "url": "https://github.com/SimonBarnett/bobiverse/pull/240",
    }
    assert mrb_row_offerable(row) is True


def test_mrb_cross_repo_pull_url_not_offerable():
    """Queue row repo must match the pull URL repo (MRB #229 hostile)."""
    row = {
        "task": "MRB",
        "repo": "SimonBarnett/bobiverse",
        "id": "#240",
        "url": "https://github.com/SimonBarnett/gh-Jeeves/pull/240",
    }
    assert mrb_row_offerable(row) is False


def test_fr_still_invents_issues_url():
    url = resolve_assign_url(
        {"task": "FR", "repo": "SimonBarnett/bobiverse", "id": "#247"}
    )
    assert url == "https://github.com/SimonBarnett/bobiverse/issues/247"


def test_decide_skips_mrb_issue_as_pull_and_offers_next(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _queue(
        home,
        [
            {
                "repo": "SimonBarnett/bobiverse",
                "task": "MRB",
                "id": "#227",
                "seq": 1,
                # synthesizable trap: no url
            },
            {
                "repo": "SimonBarnett/bobiverse",
                "task": "FR",
                "id": "#247",
                "url": "https://github.com/SimonBarnett/bobiverse/issues/247",
                "seq": 2,
            },
        ],
    )
    st = ChairAssignState(timeout_s=300)
    st.bind(home)
    d = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d.action == "assign"
    assert "FR SimonBarnett/bobiverse#247" in (d.line or "")
    assert "pull/227" not in (d.line or "")


def test_decide_skips_mrb_when_pr_exists_false(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _queue(
        home,
        [
            {
                "repo": "SimonBarnett/bobiverse",
                "task": "MRB",
                "id": "#227",
                "url": "https://github.com/SimonBarnett/bobiverse/pull/227",
                "seq": 1,
            },
            {
                "repo": "SimonBarnett/bobiverse",
                "task": "FR",
                "id": "#10",
                "url": "https://github.com/SimonBarnett/bobiverse/issues/10",
                "seq": 2,
            },
        ],
    )
    st = ChairAssignState(timeout_s=300)
    st.bind(home)

    def pr_exists(repo: str, num: str) -> bool:
        return False  # pull/227 404

    d = st.decide(
        home,
        "marchhare-1",
        "#marchhare",
        live_nicks={"marchhare-1"},
        pr_exists=pr_exists,
    )
    assert d.action == "assign"
    assert "FR SimonBarnett/bobiverse#10" in (d.line or "")
