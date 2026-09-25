"""K2 CAST IRON: claim/ear paths accept live ``{machine}-{pid}`` nicks.

FR #3 / brief K2. Legacy agentic_irc ``bored_gate`` only matched ``w-<short>-<pid>``
and ignored seats like ``marchhare-34992``. gh-Jeeves primary grammar is live seats.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.nicks import (
    bored_gate,
    canonical_worker_nick,
    is_live_seat_nick,
    is_worker_nick,
    nick_matches_shop,
    parse_worker_nick,
    worker_shop_channel,
)
from jeeves.queue import load_queue, save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


@pytest.mark.parametrize(
    "nick,machine,pid",
    [
        ("flamingo-46804", "flamingo", "46804"),
        ("marchhare-34992", "marchhare", "34992"),
        ("ionos-1", "ionos", "1"),
        ("ce-priority-dev1-12345", "ce-priority-dev1", "12345"),
        ("Flamingo-99", "flamingo", "99"),
    ],
)
def test_k2_live_machine_pid_grammar(nick, machine, pid):
    assert parse_worker_nick(nick) == (machine, pid)
    assert is_worker_nick(nick)
    assert is_live_seat_nick(nick)
    assert canonical_worker_nick(nick) == f"{machine}-{pid}"
    assert worker_shop_channel(nick) == f"#{machine}"
    assert nick_matches_shop(nick, f"#{machine}")
    assert not nick_matches_shop(nick, "#other")


def test_k2_legacy_w_still_accepted():
    assert parse_worker_nick("w-mh-123") == ("mh", "123")
    assert canonical_worker_nick("w-fla-99") == "fla-99"
    assert is_worker_nick("w-fla-99")
    assert not is_live_seat_nick("w-fla-99")


@pytest.mark.parametrize(
    "nick",
    ["Jeeves", "bob-flamingo", "bob-ionos", "simon", "nickserv", "justtext", "123-456"],
)
def test_k2_bots_and_junk_rejected(nick):
    assert parse_worker_nick(nick) is None
    assert not is_worker_nick(nick)
    assert bored_gate(None, nick, "#flamingo", skip_idle_check=True) == "not_worker"


def test_k2_bored_gate_accepts_live_seat_not_w_only():
    """The bug: w-only gate would return not_worker for marchhare-34992."""
    assert bored_gate(None, "marchhare-34992", "#marchhare", skip_idle_check=True) == "ok"
    assert bored_gate(None, "flamingo-46804", "#flamingo", skip_idle_check=True) == "ok"
    assert bored_gate(None, "flamingo-46804", "#marchhare", skip_idle_check=True) == "wrong_shop"


def test_k2_bored_gate_idle_wait(tmp_path: Path):
    from jeeves.nicks import note_worker_activity

    home = tmp_path / "h"
    home.mkdir()
    now = time.time()
    note_worker_activity(home, "flamingo-1", now=now)
    assert bored_gate(home, "flamingo-1", "#flamingo", now=now + 10, idle_s=120) == "wait"
    assert bored_gate(home, "flamingo-1", "#flamingo", now=now + 200, idle_s=120) == "ok"


def test_k2_no_w_only_regex_in_bored_gate_source():
    import inspect
    import jeeves.nicks as nicks

    src = inspect.getsource(nicks.bored_gate)
    # must not hard-require leading w-
    assert "w-<" not in src
    assert r"^w-" not in src


def test_k2_ear_offers_to_live_seat_nick(tmp_path: Path):
    home = tmp_path / "digest"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "SimonBarnett/gh-Jeeves",
                    "task": "FR",
                    "id": "#3",
                    "seq": 1,
                    "line": "K2",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/3",
                }
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
    base = f"http://127.0.0.1:{rport}"

    # Real live seat shape (the one legacy gate ignored)
    seat = "marchhare-34992"
    # Use flamingo shop with flamingo-shaped nick for single-machine ear under test
    seat = "flamingo-34992"

    jeeves = JeevesChair("127.0.0.1", port, home, base, shops=["#flamingo"])
    jeeves.live_seats_override = {seat}
    ear = BobEar("127.0.0.1", port, home, machine="flamingo")
    worker = IrcClient("127.0.0.1", port, seat)
    worker.join("#flamingo")
    jeeves.start()
    ear.start()
    time.sleep(0.15)
    try:
        worker.privmsg("#flamingo", "!bored")
        # FR #106: Jeeves assigns (ear OFFER retired)
        assign = worker.wait_privmsg(
            predicate=lambda m: m[0].lower() == "jeeves"
            and m[2].startswith(f"{seat}:")
            and "OFFER" not in m[2],
            timeout=5.0,
        )
        assert assign is not None, f"jeeves={jeeves.handled} ear={ear.offers}"
        assert "FR SimonBarnett/gh-Jeeves#3" in assign[2]

        worker.privmsg("#flamingo", "ACK FR SimonBarnett/gh-Jeeves#3")
        deadline = time.time() + 5
        while time.time() < deadline and not any(
            h.startswith(f"ack:{seat}:") for h in jeeves.handled
        ):
            time.sleep(0.05)
        assert any(f"ack:{seat}:SimonBarnett/gh-Jeeves#3" in h for h in jeeves.handled)
        q = load_queue(home)
        assert q["accepted"][0]["nick"] == seat
    finally:
        jeeves.stop()
        ear.stop()
        worker.close()
        rx.stop()
        ircd.stop()


def test_k2_g1_grammar_regression():
    """Keep G1 expectation: flamingo-46804 is a worker."""
    assert parse_worker_nick("flamingo-46804") == ("flamingo", "46804")
    assert canonical_worker_nick("w-fla-99") == "fla-99"
    assert parse_worker_nick("Jeeves") is None
