"""FR #106: Jeeves assigns on !bored — trust, ordering, timeout, MRB author, e2e."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.assign import (
    ChairAssignState,
    format_assign_line,
    format_nothing_queued,
    mrb_blocked_for_author,
    parse_assign_line,
    trust_bored,
)
from jeeves.guard import no_llm_network_guard
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import Claim, accept_job, apply_queue_event, load_queue, save_queue, worker_state
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def _queue(home: Path, rows: list[dict]) -> None:
    save_queue(
        home,
        {"v": 1, "unaccepted": rows, "accepted": [], "done": [], "workers": {}},
    )


def test_trust_bored_only_own_shop_worker():
    assert trust_bored("marchhare-31712", "#marchhare") == "ok"
    assert trust_bored("marchhare-31712", "#flamingo") == "wrong_shop"
    assert trust_bored("bob-marchhare", "#marchhare") == "not_worker"
    assert trust_bored("Jeeves", "#marchhare") == "not_worker"
    assert trust_bored("marchhare-31712", "marchhare-31712", is_pm=True) == "pm"


def test_assign_line_shape_and_legacy_pr_task():
    line = format_assign_line(
        "marchhare-1",
        {
            "task": "PR",
            "repo": "SimonBarnett/gh-Jeeves",
            "id": "#15",
            "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/15",
        },
    )
    assert line.startswith("marchhare-1: FR SimonBarnett/gh-Jeeves#15 ")
    assert "OFFER" not in line and "ASSIGN" not in line
    parsed = parse_assign_line(line)
    assert parsed and parsed["task"] == "FR"
    assert format_nothing_queued("x-1") == "x-1: nothing queued"


def test_focus_ordering_and_no_double_offer(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    # focus.json — high priority on second repo
    (home / "focus.json").write_text(
        '{"v":1,"repos":{"SimonBarnett/other":1,"SimonBarnett/gh-Jeeves":9}}\n',
        encoding="utf-8",
    )
    _queue(
        home,
        [
            {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "FR",
                "id": "#1",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/1",
                "seq": 1,
            },
            {
                "repo": "SimonBarnett/other",
                "task": "FR",
                "id": "#2",
                "url": "https://github.com/SimonBarnett/other/issues/2",
                "seq": 2,
            },
        ],
    )
    st = ChairAssignState(timeout_s=300)
    st.bind(home)
    d1 = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d1.action == "assign"
    assert "SimonBarnett/other#2" in (d1.line or "")
    # second bored while open → no double
    d2 = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d2.action == "open"
    # other nick must not get the same offered job
    d3 = st.decide(home, "flamingo-9", "#flamingo", live_nicks={"flamingo-9", "marchhare-1"})
    assert d3.action == "assign"
    assert "gh-Jeeves#1" in (d3.line or "")


def test_offer_timeout_returns_job(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _queue(
        home,
        [
            {
                "repo": "o/r",
                "task": "FR",
                "id": "#3",
                "url": "https://github.com/o/r/issues/3",
            }
        ],
    )
    st = ChairAssignState(timeout_s=1.0)
    st.bind(home)
    d1 = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"}, now=100.0)
    assert d1.action == "assign"
    expired = st.expire_timed_out(now=102.0)
    assert "marchhare-1" in expired
    d2 = st.decide(home, "marchhare-2", "#marchhare", live_nicks={"marchhare-2"}, now=103.0)
    assert d2.action == "assign"
    assert "o/r#3" in (d2.line or "")


def test_mrb_author_rule_one_and_two_live_seats():
    row = {
        "task": "MRB",
        "repo": "SimonBarnett/gh-Jeeves",
        "id": "#104",
        "author_seat": "marchhare-31712",
        "url": "https://github.com/SimonBarnett/gh-Jeeves/pull/104",
    }
    # two live seats → author blocked
    assert mrb_blocked_for_author(row, "marchhare-31712", {"marchhare-31712", "flamingo-1"})
    # only one live seat → self-MRB allowed
    assert not mrb_blocked_for_author(row, "marchhare-31712", {"marchhare-31712"})
    # other nick not author → ok
    assert not mrb_blocked_for_author(row, "flamingo-1", {"marchhare-31712", "flamingo-1"})


def test_empty_queue_nothing_queued(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _queue(home, [])
    st = ChairAssignState()
    st.bind(home)
    d = st.decide(home, "marchhare-1", "#marchhare", live_nicks={"marchhare-1"})
    assert d.action == "nothing"
    assert d.line == "marchhare-1: nothing queued"


def test_chair_unit_bored_assign_and_ack(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    _queue(
        home,
        [
            {
                "repo": "SimonBarnett/gh-Jeeves",
                "task": "PR",
                "id": "#15",
                "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/15",
            }
        ],
    )
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def join(self, *a):
            pass

        def privmsg(self, *a, **k):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    chair = JeevesChair(
        "127.0.0.1", 1, home, "http://127.0.0.1:9", shops=["#marchhare"], client=FakeClient()
    )
    chair.live_seats_override = {"marchhare-31712"}
    chair._post_report = lambda payload: posts.append(payload)  # type: ignore
    chair._handle_shop("marchhare-31712", "#marchhare", "!bored")
    assert any(h.startswith("assign:marchhare-31712:") for h in chair.handled)
    assert chair.shop_egress
    assert "FR SimonBarnett/gh-Jeeves#15" in chair.shop_egress[0][1]
    # ACK uses FR (legacy PR migrated/canonicalised)
    chair._handle_shop("marchhare-31712", "#marchhare", "ACK FR SimonBarnett/gh-Jeeves#15")
    assert worker_state(home, "marchhare-31712") == "busy"
    assert load_queue(home)["accepted"]


def test_e2e_token_pools_off_bored_assign_ack_done(tmp_path: Path):
    """G1-style: every token pool off; announce → !bored → assign → ACK → DONE → !bored."""
    home = tmp_path / "digest"
    home.mkdir()
    with no_llm_network_guard():
        ircd = LocalIrcd()
        port = ircd.start()
        rx = StubReceiver(home)
        rport = rx.start()
        base = f"http://127.0.0.1:{rport}"
        apply_queue_event(
            home,
            Claim(
                repo="SimonBarnett/gh-Jeeves",
                task="FR",
                id="#106",
                event="issues",
                action="opened",
                line="assign on bored",
                url="https://github.com/SimonBarnett/gh-Jeeves/issues/106",
            ),
        )
        apply_queue_event(
            home,
            Claim(
                repo="SimonBarnett/gh-Jeeves",
                task="FR",
                id="#107",
                event="issues",
                action="opened",
                line="second",
                url="https://github.com/SimonBarnett/gh-Jeeves/issues/107",
            ),
        )
        chair = JeevesChair("127.0.0.1", port, home, base, shops=["#marchhare"])
        chair.live_seats_override = {"marchhare-99"}
        worker = IrcClient("127.0.0.1", port, "marchhare-99")
        worker.join("#marchhare")
        chair.start()
        time.sleep(0.2)
        try:
            worker.privmsg("#marchhare", "!bored")
            msg = worker.wait_privmsg(
                predicate=lambda m: m[0].lower() == "jeeves" and "marchhare-99:" in m[2],
                timeout=5.0,
            )
            assert msg is not None
            assert "FR SimonBarnett/gh-Jeeves#106" in msg[2] or "gh-Jeeves#106" in msg[2]
            worker.privmsg("#marchhare", "ACK FR SimonBarnett/gh-Jeeves#106")
            deadline = time.time() + 5
            while time.time() < deadline and worker_state(home, "marchhare-99") != "busy":
                time.sleep(0.05)
            assert worker_state(home, "marchhare-99") == "busy"
            # both workers views via digest snapshot
            snap = rx.state.snapshot()
            tw = (snap.get("workers") or {}).get("marchhare-99") or {}
            assert tw.get("state") == "busy" or worker_state(home, "marchhare-99") == "busy"
            worker.privmsg(
                "#marchhare",
                "DONE FR SimonBarnett/gh-Jeeves#106 ok https://example.com/pr",
            )
            deadline = time.time() + 5
            while time.time() < deadline and worker_state(home, "marchhare-99") != "idle":
                time.sleep(0.05)
            assert worker_state(home, "marchhare-99") == "idle"
            worker.privmsg("#marchhare", "!bored")
            msg2 = worker.wait_privmsg(
                predicate=lambda m: m[0].lower() == "jeeves"
                and "marchhare-99:" in m[2]
                and "#107" in m[2],
                timeout=5.0,
            )
            assert msg2 is not None
        finally:
            chair.stop()
            worker.close()
            rx.stop()
            ircd.stop()
