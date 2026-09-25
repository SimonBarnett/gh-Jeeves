"""K2 CAST IRON: claim / !bored accept real ``{machine}-{pid}`` nicks.

FR #3 / brief K2. Legacy bored_gate only allowed ``w-<short>-<pid>`` and
ignored live seats (e.g. marchhare-34992). Outcome: one grammar
``{machine}-{pid}`` for everything, with tests.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.nicks import (
    bored_gate,
    canonical_worker_nick,
    is_worker_nick,
    parse_worker_nick,
    worker_in_own_shop,
    worker_shop_channel,
)
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


@pytest.mark.parametrize(
    "nick,machine,pid",
    [
        ("flamingo-46804", "flamingo", "46804"),
        ("marchhare-34992", "marchhare", "34992"),
        ("ce-priority-dev1-61432", "ce-priority-dev1", "61432"),
        ("ionos-1", "ionos", "1"),
        ("FLAMINGO-99", "flamingo", "99"),
    ],
)
def test_k2_primary_machine_pid_grammar(nick: str, machine: str, pid: str):
    assert parse_worker_nick(nick) == (machine, pid)
    assert is_worker_nick(nick) is True
    assert canonical_worker_nick(nick) == f"{machine}-{pid}"
    assert worker_shop_channel(nick) == f"#{machine}"
    assert bored_gate(nick, f"#{machine}") == "ok"
    assert worker_in_own_shop(nick, f"#{machine}") is True
    assert worker_in_own_shop(nick, "#bobiverse") is False
    assert bored_gate(nick, "#other") == "wrong_shop"


@pytest.mark.parametrize(
    "nick",
    [
        "w-fla-99",
        "w-mh-12345",
        "W-ion-7",
    ],
)
def test_k2_legacy_w_short_still_parsed(nick: str):
    """Legacy accepted and canonicalised — not the only allowed form."""
    assert is_worker_nick(nick)
    canon = canonical_worker_nick(nick)
    assert canon is not None
    assert not canon.lower().startswith("w-")
    assert parse_worker_nick(nick) is not None


@pytest.mark.parametrize(
    "nick",
    [
        "Jeeves",
        "bob-flamingo",
        "bob-marchhare",
        "simon",
        "flamingo",  # no pid
        "w-123",  # not w-short-pid
        "",
        "nickserv",
    ],
)
def test_k2_non_workers_rejected(nick: str):
    assert is_worker_nick(nick) is False
    assert canonical_worker_nick(nick) is None
    assert bored_gate(nick, "#flamingo") == "not_worker"


def test_k2_not_only_legacy_w_required():
    """Regression: live seat must pass where pure w-* gate would fail."""
    live = "marchhare-34992"
    legacy_only_pattern_would_reject = not live.lower().startswith("w-")
    assert legacy_only_pattern_would_reject
    assert bored_gate(live, "#marchhare") == "ok"


def test_k2_ear_offers_to_machine_pid_seat(tmp_path: Path):
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

    # Multi-hyphen machine style seat
    nick = "flamingo-34992"
    jeeves = JeevesChair("127.0.0.1", port, home, base, shops=["#flamingo"])
    ear = BobEar("127.0.0.1", port, home, machine="flamingo")
    worker = IrcClient("127.0.0.1", port, nick)
    worker.join("#flamingo")
    jeeves.start()
    ear.start()
    time.sleep(0.15)
    try:
        worker.privmsg("#flamingo", "!bored")
        offer = worker.wait_privmsg(
            predicate=lambda m: m[0].startswith("bob-") and "OFFER" in m[2],
            timeout=5.0,
        )
        assert offer is not None
        assert offer[2].startswith(f"{nick}:")
        assert "OFFER FR SimonBarnett/gh-Jeeves#3" in offer[2]

        worker.privmsg("#flamingo", "ACK FR SimonBarnett/gh-Jeeves#3")
        deadline = time.time() + 5
        while time.time() < deadline and not any(
            h.startswith(f"ack:{nick}:") for h in jeeves.handled
        ):
            time.sleep(0.05)
        assert any(f"ack:{nick}:SimonBarnett/gh-Jeeves#3" in h for h in jeeves.handled)
    finally:
        jeeves.stop()
        ear.stop()
        worker.close()
        rx.stop()
        ircd.stop()


def test_k2_jeeves_ignores_ack_from_bob_ear_nick(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [
                {
                    "repo": "o/r",
                    "task": "FR",
                    "id": "#1",
                    "seq": 1,
                    "line": "x",
                    "url": "http://x",
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
    j = JeevesChair("127.0.0.1", port, home, f"http://127.0.0.1:{rport}", shops=["#flamingo"])
    # Simulate shop line from non-worker by feeding handler directly
    j._handle_shop("bob-flamingo", "#flamingo", "ACK FR o/r#1")
    assert any("ignored_ack_bad_nick" in h for h in j.handled)
    j.stop()
    rx.stop()
    ircd.stop()
