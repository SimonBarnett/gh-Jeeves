"""FR #50: in-channel !list → PM only; filters; no silent 10-row cap."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest

from jeeves.commands import get_command
from jeeves.listfmt import LIST_LINE_MAX, format_list_line, format_unaccepted_list, reset_list_rate
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair
from jeeves.wire import is_list, parse_list_filters


def _row(task: str, repo: str, n: int, title: str, seq: int | None = None) -> dict:
    return {
        "task": task,
        "repo": repo,
        "id": f"#{n}",
        "line": title,
        "seq": seq if seq is not None else n,
        "url": f"https://github.com/{repo}/issues/{n}",
    }


def test_is_list_multi_args():
    assert is_list("!list")
    assert is_list("!list all")
    assert is_list("!list SimonBarnett/gh-Jeeves")
    assert is_list("!list all SimonBarnett/gh-Jeeves")
    assert is_list("!list fr")
    assert not is_list("!help")


def test_parse_list_filters():
    assert parse_list_filters("!list") == (None, None, False)
    assert parse_list_filters("!list all")[2] is True
    assert parse_list_filters("!list SimonBarnett/gh-Jeeves")[1] == "SimonBarnett/gh-Jeeves"
    assert parse_list_filters("!list fr")[0] == "FR"


def test_format_mode_repo_hash_title():
    line = format_list_line(_row("FR", "SimonBarnett/gh-Jeeves", 50, "in-channel list"))
    assert line.startswith("FR SimonBarnett/gh-Jeeves#50")
    assert "in-channel list" in line
    assert len(line.encode("utf-8")) <= LIST_LINE_MAX


def test_no_silent_cap_emits_more_hint(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    rows = [_row("FR", "o/r", i, f"t{i}", seq=i) for i in range(1, 40)]
    save_queue(
        home,
        {"v": 1, "unaccepted": rows, "accepted": [], "done": [], "workers": {}},
    )
    lines = format_unaccepted_list(home, max_lines=10)
    assert any("more" in ln.lower() for ln in lines)
    assert not any(ln.startswith("queue empty") for ln in lines)
    # job lines use MODE repo#n
    body = "\n".join(lines)
    assert "FR o/r#1" in body


def test_list_all_includes_accepted(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [_row("FR", "a/b", 1, "open")],
            "accepted": [
                {
                    **_row("MRB", "a/b", 2, "busy"),
                    "nick": "flamingo-1",
                }
            ],
            "done": [],
            "workers": {},
        },
    )
    only_u = format_unaccepted_list(home, list_all=False)
    assert "MRB a/b#2" not in "\n".join(only_u)
    all_lines = format_unaccepted_list(home, list_all=True)
    blob = "\n".join(all_lines)
    assert "FR a/b#1" in blob
    assert "MRB a/b#2" in blob


def test_list_repo_filter(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("FR", "SimonBarnett/gh-Jeeves", 50, "x"),
                _row("FR", "other/repo", 1, "y"),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    lines = format_unaccepted_list(home, repo_filter="SimonBarnett/gh-Jeeves")
    blob = "\n".join(lines)
    assert "gh-Jeeves#50" in blob
    assert "other/repo" not in blob


def test_help_list_matches_registry():
    spec = get_command("list")
    assert spec is not None
    assert "all" in spec.syntax
    assert "repo" in spec.syntax.lower() or "<repo>" in spec.syntax
    assert "PM" in spec.summary or "pm" in spec.details.lower()


def test_channel_list_pm_not_channel_sniffer(tmp_path: Path):
    """In-channel !list → PM to asker; sniffer sees no Jeeves channel list dump."""
    reset_list_rate()
    home = tmp_path / "digest"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("FR", "SimonBarnett/gh-Jeeves", 50, "list fix", seq=1),
                _row("MRB", "SimonBarnett/gh-Jeeves", 51, "other", seq=2),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    j = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#flamingo"],
    )
    asker = IrcClient("127.0.0.1", port, "simon")
    asker.join("#flamingo")
    sniffer = IrcClient("127.0.0.1", port, "sniff-list")
    sniffer.join("#flamingo")
    j.start()
    time.sleep(0.15)
    try:
        with mock.patch("jeeves.roles.FLOOD_S", 0):
            asker.privmsg("#flamingo", "!list")
            deadline = time.time() + 5
            while time.time() < deadline and not j.pm_egress:
                time.sleep(0.05)
        assert j.pm_egress, f"expected PM; handled={j.handled}"
        assert all(n == "simon" for n, _ in j.pm_egress)
        blob = "\n".join(t for _, t in j.pm_egress)
        assert "FR SimonBarnett/gh-Jeeves#50" in blob
        assert "MRB SimonBarnett/gh-Jeeves#51" in blob

        time.sleep(0.3)
        sniffer.wait_privmsg(timeout=0.2)
        jeeves_ch = [
            m
            for m in sniffer.inbox
            if m[0].lower() == "jeeves"
            and m[1].lower() == "#flamingo"
            and ("FR " in m[2] or "MRB " in m[2] or "unaccepted" in m[2].lower())
        ]
        assert jeeves_ch == [], f"channel flood: {jeeves_ch}"
        assert any(h.startswith("list_pm:simon:") for h in j.handled)
    finally:
        j.stop()
        asker.close()
        sniffer.close()
        rx.stop()
        ircd.stop()
