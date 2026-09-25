"""FR #113: !focus owner/repo#N ranks ahead of repo-level focus."""
from __future__ import annotations

import json
from pathlib import Path

from jeeves.focus import (
    format_focus_lines,
    handle_focus_cmd,
    handle_unfocus_cmd,
    load_focus,
    save_focus,
    set_focus,
    set_item_focus,
    sort_unaccepted_rows,
)
from jeeves.queue import Claim, apply_queue_event, save_queue


def _rows(*specs: tuple[str, str, str, int]) -> list[dict]:
    """(repo, task, id, seq) -> queue rows."""
    out = []
    for repo, task, ident, seq in specs:
        out.append(
            {
                "repo": repo,
                "task": task,
                "id": ident if ident.startswith("#") else f"#{ident}",
                "seq": seq,
                "line": f"{task} {ident}",
                "url": f"https://github.com/{repo}/issues/{ident.lstrip('#')}",
            }
        )
    return out


def test_item_focus_beats_repo_focus(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_focus(home, "SimonBarnett/gh-Jeeves", priority=1, label="high")
    set_item_focus(home, "SimonBarnett/other#5", rank=1)
    rows = _rows(
        ("SimonBarnett/gh-Jeeves", "FR", "#1", 1),
        ("SimonBarnett/other", "FR", "#5", 99),
    )
    ordered = sort_unaccepted_rows(home, rows)
    assert ordered[0]["id"] == "#5"
    assert ordered[0]["repo"] == "SimonBarnett/other"


def test_items_in_rank_order(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_item_focus(home, "a/b#2", rank=2)
    set_item_focus(home, "a/b#1", rank=1)
    rows = _rows(("a/b", "FR", "#2", 1), ("a/b", "FR", "#1", 2))
    ordered = sort_unaccepted_rows(home, rows)
    assert [r["id"] for r in ordered] == ["#1", "#2"]


def test_closed_item_dropped(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_item_focus(home, "SimonBarnett/gh-Jeeves#110", rank=1)
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [],
            "accepted": [],
            "done": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#110",
                    "result": "closed",
                }
            ],
            "workers": {},
        },
    )
    # purge runs via load/sort path
    from jeeves.focus import purge_stale_item_focus

    removed = purge_stale_item_focus(home)
    assert "SimonBarnett/gh-Jeeves#110" in removed or any(
        "110" in x for x in removed
    )
    doc = load_focus(home)
    assert not (doc.get("items") or {})


def test_focus_carries_fr_to_mrb(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_item_focus(home, "SimonBarnett/gh-Jeeves#19", rank=1)
    apply_queue_event(
        home,
        Claim(
            repo="SimonBarnett/gh-Jeeves",
            task="FR",
            id="#19",
            event="issues",
            action="opened",
            line="fr",
            url="https://github.com/SimonBarnett/gh-Jeeves/issues/19",
        ),
    )
    apply_queue_event(
        home,
        Claim(
            repo="SimonBarnett/gh-Jeeves",
            task="MRB",
            id="#42",
            event="pull_request",
            action="opened",
            line="pr Closes #19",
            url="https://github.com/SimonBarnett/gh-Jeeves/pull/42",
            refs=("#19",),
            pr_id="#42",
        ),
    )
    doc = load_focus(home)
    items = doc.get("items") or {}
    # focus moved to MRB #42
    assert any(k.lower().endswith("#42") for k in items)
    assert not any(k.lower().endswith("#19") for k in items)


def test_old_focus_json_still_loads(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    (home / "focus.json").write_text(
        json.dumps(
            {
                "v": 1,
                "repos": {
                    "SimonBarnett/gh-Jeeves": {
                        "priority": 1,
                        "label": "high",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                },
                "updated": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    doc = load_focus(home)
    assert "SimonBarnett/gh-Jeeves" in (doc.get("repos") or {})
    assert isinstance(doc.get("items"), dict)


def test_focus_cmd_parses_item_and_short_form(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    lines = handle_focus_cmd(home, "SimonBarnett/gh-Jeeves#110")
    assert any("#110" in x for x in lines)
    lines2 = handle_focus_cmd(home, "other#5 1")
    assert any("#5" in x for x in lines2)
    bare = format_focus_lines(home)
    assert any("#110" in x or "#5" in x for x in bare)
    # unfocus item
    u = handle_unfocus_cmd(home, "SimonBarnett/gh-Jeeves#110")
    assert any("removed" in x.lower() or "unfocus" in x.lower() for x in u)
