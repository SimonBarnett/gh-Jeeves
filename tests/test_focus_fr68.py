"""FR #68: !focus priority-sort for !list and !bored (top_unaccepted)."""

from __future__ import annotations

import time
from pathlib import Path

from jeeves.digest import public_digest_snapshot
from jeeves.focus import (
    DEFAULT_PRIORITY,
    NAMED_PRIORITY,
    focus_path,
    handle_focus_cmd,
    handle_unfocus_cmd,
    load_focus,
    set_focus,
    sort_unaccepted_rows,
)
from jeeves.listfmt import format_unaccepted_list
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.offer import EarOfferState
from jeeves.queue import load_queue, save_queue, top_unaccepted
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def _row(repo: str, n: int, seq: int, title: str = "t") -> dict:
    return {
        "task": "FR",
        "repo": repo,
        "id": f"#{n}",
        "line": title,
        "seq": seq,
        "url": f"https://github.com/{repo}/issues/{n}",
    }


def test_named_and_numeric_priority_sort(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("o/old", 1, seq=1, title="oldest unfocused"),
                _row("o/med", 2, seq=2, title="medium"),
                _row("o/hi", 3, seq=3, title="high"),
                _row("o/low", 4, seq=4, title="low"),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    set_focus(home, "o/hi", priority=NAMED_PRIORITY["high"], label="high")
    set_focus(home, "o/med", priority=NAMED_PRIORITY["medium"], label="medium")
    set_focus(home, "o/low", priority=NAMED_PRIORITY["low"], label="low")
    ordered = [r["repo"] for r in sort_unaccepted_rows(home, list(load_queue(home)["unaccepted"]))]
    assert ordered[0] == "o/hi"
    assert ordered[1] == "o/med"
    assert ordered[2] == "o/low"
    assert ordered[3] == "o/old"
    top = top_unaccepted(home)
    assert top is not None and top["repo"] == "o/hi"
    lines = format_unaccepted_list(home)
    blob = "\n".join(lines)
    # high job appears before old
    assert blob.index("o/hi") < blob.index("o/old")
    assert "[high]" in blob


def test_focus_1_before_2_list_and_bored(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("a/second", 10, seq=1),
                _row("a/first", 11, seq=2),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    handle_focus_cmd(home, "2 a/second")
    handle_focus_cmd(home, "1 a/first")
    assert top_unaccepted(home)["repo"] == "a/first"
    lines = "\n".join(format_unaccepted_list(home))
    assert lines.index("a/first") < lines.index("a/second")
    ear = EarOfferState()
    d = ear.decide(home, "ionos-9001", machine="ionos")
    assert d.action == "offer"
    assert "a/first" in (d.line or "")


def test_unfocus_restores_seq_order(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("r/a", 1, seq=1),
                _row("r/b", 2, seq=2),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    set_focus(home, "r/b", priority=1, label="high")
    assert top_unaccepted(home)["repo"] == "r/b"
    handle_unfocus_cmd(home, "r/b")
    assert top_unaccepted(home)["repo"] == "r/a"
    handle_focus_cmd(home, "r/b")
    handle_unfocus_cmd(home, "all")
    assert top_unaccepted(home)["repo"] == "r/a"
    assert load_focus(home)["repos"] == {}


def test_focus_persists_restart(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_focus(home, "SimonBarnett/gh-Jeeves", priority=1, label="high")
    assert focus_path(home).is_file()
    # reload
    doc = load_focus(home)
    assert "SimonBarnett/gh-Jeeves" in doc["repos"]
    assert int(doc["repos"]["SimonBarnett/gh-Jeeves"]["priority"]) == 1


def test_reset_priority_changes_order(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("x/a", 1, seq=1),
                _row("x/b", 2, seq=2),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    handle_focus_cmd(home, "1 x/a")
    handle_focus_cmd(home, "2 x/b")
    assert top_unaccepted(home)["repo"] == "x/a"
    handle_focus_cmd(home, "1 x/b")
    handle_focus_cmd(home, "9 x/a")
    assert top_unaccepted(home)["repo"] == "x/b"


def test_url_normalize_and_bare_focus_is_high(tmp_path: Path):
    home = tmp_path
    lines = handle_focus_cmd(home, "https://github.com/SimonBarnett/AgentMonitor")
    assert any("AgentMonitor" in ln for ln in lines)
    assert any("high" in ln for ln in lines)
    doc = load_focus(home)
    assert any("AgentMonitor" in k for k in doc["repos"])


def test_digest_exposes_focus(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    set_focus(home, "o/r", priority=5, label="medium")
    snap = public_digest_snapshot(home)
    assert "machines" in snap
    assert "queue" in snap
    assert isinstance(snap.get("focus"), list)
    assert snap["focus"][0]["repo"] == "o/r"
    assert snap["focus"][0]["priority"] == 5
    # FR #154: additive boolean; default off when never toggled
    assert snap.get("focus_strict") is False


def test_non_simon_refused(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    chair._outbox.flood_s = 0.0
    chair.start()
    time.sleep(0.1)
    try:
        w = IrcClient("127.0.0.1", port, "ionos-1")
        w.privmsg("Jeeves", "!focus high o/r")
        deadline = time.time() + 2
        while time.time() < deadline and not any(
            "focus_denied" in h for h in chair.handled
        ):
            time.sleep(0.05)
        assert any("focus_denied" in h for h in chair.handled)
        assert not focus_path(home).is_file() or load_focus(home)["repos"] == {}
    finally:
        chair.stop()
        w.close()
        rx.stop()
        ircd.stop()


def test_simon_with_account_can_focus(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    if chair.mode_grants is not None:
        chair.mode_grants.state.accounts["simon"] = "simon"
    chair._outbox.flood_s = 0.0
    chair.start()
    time.sleep(0.1)
    try:
        simon = IrcClient("127.0.0.1", port, "simon")
        simon.privmsg("Jeeves", "!focus 1 SimonBarnett/gh-Jeeves")
        deadline = time.time() + 3
        while time.time() < deadline and not any(
            h.startswith("focus:simon:") for h in chair.handled
        ):
            time.sleep(0.05)
        assert any(h.startswith("focus:simon:") for h in chair.handled), chair.handled
        assert "SimonBarnett/gh-Jeeves" in load_focus(home)["repos"]
        simon.privmsg("Jeeves", "!focus")
        time.sleep(0.3)
        assert any("gh-Jeeves" in t for _, t in chair.pm_egress)
    finally:
        chair.stop()
        simon.close()
        rx.stop()
        ircd.stop()


def test_help_registers_focus():
    from jeeves.commands import get_command
    from jeeves.helpcmd import build_help

    assert get_command("focus") is not None
    assert get_command("unfocus") is not None
    simon = "\n".join(build_help("simon", include_shop_pointer=False).lines)
    assert "focus" in simon.lower()
    worker = "\n".join(build_help("flamingo-1", include_shop_pointer=False).lines)
    assert not any(ln.strip().lower().startswith("!focus") for ln in worker.splitlines())


def test_ignored_still_hidden_when_focused(tmp_path: Path):
    from jeeves.ignore import add_ignore

    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                _row("z/ignored", 1, seq=1),
                _row("z/ok", 2, seq=2),
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    set_focus(home, "z/ignored", priority=1, label="high")
    add_ignore(home, "z/ignored")
    top = top_unaccepted(home)
    assert top is not None and top["repo"] == "z/ok"
    blob = "\n".join(format_unaccepted_list(home))
    assert "ignored" not in blob
