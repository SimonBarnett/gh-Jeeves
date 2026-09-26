"""FR #141: !focus strict on|off — assign only focused jobs; empty → nothing queued."""
from __future__ import annotations

from pathlib import Path

from jeeves.assign import ChairAssignState
from jeeves.focus import (
    NAMED_PRIORITY,
    handle_focus_cmd,
    is_strict_focus,
    load_focus,
    set_focus,
    set_item_focus,
    set_strict_focus,
)
from jeeves.queue import ordered_unaccepted, save_queue
from jeeves.wire import is_focus


def _row(repo: str, n: int, seq: int, title: str = "t") -> dict:
    return {
        "task": "FR",
        "repo": repo,
        "id": f"#{n}",
        "line": title,
        "seq": seq,
        "url": f"https://github.com/{repo}/issues/{n}",
    }


def _seed_queue(home: Path, rows: list[dict]) -> None:
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": rows,
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )


def test_strict_on_empty_focus_nothing_queued(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _seed_queue(
        home,
        [
            _row("SimonBarnett/agentic_build", 34, 1),
            _row("SimonBarnett/gh-Jeeves", 141, 2),
        ],
    )
    set_strict_focus(home, True)
    assert is_strict_focus(home) is True
    assert ordered_unaccepted(home) == []
    st = ChairAssignState()
    st.bind(home)
    decision = st.decide(home, "marchhare-31712", "#marchhare")
    assert decision.action == "nothing"
    assert decision.line == "marchhare-31712: nothing queued"


def test_strict_off_keeps_current_behaviour(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _seed_queue(
        home,
        [
            _row("SimonBarnett/agentic_build", 34, 1),
            _row("SimonBarnett/gh-Jeeves", 141, 2),
        ],
    )
    set_strict_focus(home, False)
    ordered = ordered_unaccepted(home)
    assert len(ordered) == 2
    assert ordered[0]["repo"] == "SimonBarnett/agentic_build"
    st = ChairAssignState()
    st.bind(home)
    decision = st.decide(home, "marchhare-31712", "#marchhare")
    assert decision.action == "assign"
    assert decision.row is not None
    assert decision.row["repo"] == "SimonBarnett/agentic_build"


def test_strict_on_assigns_only_focused_item(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _seed_queue(
        home,
        [
            _row("SimonBarnett/agentic_build", 34, 1),
            _row("SimonBarnett/gh-Jeeves", 141, 2),
        ],
    )
    set_strict_focus(home, True)
    set_item_focus(home, "SimonBarnett/gh-Jeeves#141", rank=1)
    ordered = ordered_unaccepted(home)
    assert len(ordered) == 1
    assert ordered[0]["id"] == "#141"
    st = ChairAssignState()
    st.bind(home)
    decision = st.decide(home, "marchhare-31712", "#marchhare")
    assert decision.action == "assign"
    assert decision.row is not None
    assert decision.row["repo"] == "SimonBarnett/gh-Jeeves"
    assert "#141" in (decision.line or "")


def test_strict_on_assigns_focused_repo(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _seed_queue(
        home,
        [
            _row("SimonBarnett/agentic_build", 34, 1),
            _row("SimonBarnett/gh-Jeeves", 141, 2),
        ],
    )
    set_strict_focus(home, True)
    set_focus(home, "SimonBarnett/gh-Jeeves", priority=1, label="high")
    ordered = [r["repo"] for r in ordered_unaccepted(home)]
    assert ordered == ["SimonBarnett/gh-Jeeves"]


def test_focus_strict_cmd_persists(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    assert handle_focus_cmd(home, "strict on") == ["focus strict: on"]
    assert load_focus(home).get("strict") is True
    assert handle_focus_cmd(home, "strict") == ["focus strict: on"]
    assert handle_focus_cmd(home, "strict off") == ["focus strict: off"]
    assert is_strict_focus(home) is False
    lines = handle_focus_cmd(home, "")
    assert lines[0] == "focus strict: off"


def test_purge_closed_item_on_sort(tmp_path: Path):
    """Closed/done item focus is cleared when sorting (AgentMonitor#105 linger)."""
    home = tmp_path / "d"
    home.mkdir()
    set_item_focus(home, "SimonBarnett/AgentMonitor#105", rank=1)
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [_row("SimonBarnett/gh-Jeeves", 141, 1)],
            "accepted": [],
            "done": [
                {
                    "repo": "SimonBarnett/AgentMonitor",
                    "task": "FR",
                    "id": "#105",
                    "result": "closed",
                }
            ],
            "workers": {},
        },
    )
    set_strict_focus(home, True)
    # ordered_unaccepted → sort → purge stale #105; unfocused #141 dropped by strict.
    ordered = ordered_unaccepted(home)
    assert ordered == []
    doc = load_focus(home)
    assert not (doc.get("items") or {})


def test_strict_includes_medium_repo_focus(tmp_path: Path):
    """Strict matches any repo focus entry (title: outside !focus), not only priority=high."""
    home = tmp_path / "d"
    home.mkdir()
    _seed_queue(
        home,
        [
            _row("SimonBarnett/agentic_build", 34, 1),
            _row("SimonBarnett/gh-Jeeves", 141, 2),
        ],
    )
    set_strict_focus(home, True)
    set_focus(
        home,
        "SimonBarnett/gh-Jeeves",
        priority=NAMED_PRIORITY["medium"],
        label="medium",
    )
    ordered = [r["repo"] for r in ordered_unaccepted(home)]
    assert ordered == ["SimonBarnett/gh-Jeeves"]


def test_focus_strict_bad_flag(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    assert handle_focus_cmd(home, "strict maybe") == [
        "focus: usage !focus strict on|off"
    ]
    assert is_focus("!focus strict on")
    assert is_focus("!focus strict off")


def test_unfocus_all_keeps_strict_flag(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_strict_focus(home, True)
    set_focus(home, "SimonBarnett/gh-Jeeves", priority=1, label="high")
    from jeeves.focus import handle_unfocus_cmd

    handle_unfocus_cmd(home, "all")
    assert is_strict_focus(home) is True
    doc = load_focus(home)
    assert not (doc.get("repos") or {})
    assert doc.get("strict") is True
