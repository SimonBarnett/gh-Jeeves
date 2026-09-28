"""FR #215: ping → pong (optional nick glob); channel + PM."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.cast_iron import shop_egress_allowed_for_chair
from jeeves.guard import no_llm_network_guard
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair
from jeeves.wire import parse_ping, ping_matches


@pytest.mark.parametrize(
    "text,ok,pat",
    [
        ("ping", True, None),
        ("  PING  ", True, None),
        ("ping jeeves", True, "jeeves"),
        ("PING Jeeves", True, "Jeeves"),
        ("ping j*", True, "j*"),
        ("ping *eev*", True, "*eev*"),
        ("ping jeev?s", True, "jeev?s"),
        ("ping *", True, "*"),
        ("pingpong", False, None),
        ("pinging", False, None),
        ("ping jeeves extra", False, None),
        ("ping a b", False, None),
        ("!ping", False, None),
        ("", False, None),
    ],
)
def test_parse_ping(text, ok, pat):
    got_ok, got_pat = parse_ping(text)
    assert got_ok is ok
    assert got_pat == pat


@pytest.mark.parametrize(
    "pattern,nick,expect",
    [
        ("jeeves", "Jeeves", True),
        ("JEEVES", "Jeeves", True),
        ("j*", "Jeeves", True),
        ("*eev*", "Jeeves", True),
        ("jeev?s", "Jeeves", True),
        ("*", "Jeeves", True),
        ("bob-*", "Jeeves", False),
        ("simon", "Jeeves", False),
        ("x*", "Jeeves", False),
        ("jeeves-*", "Jeeves-dev", True),
        ("jeeves", "Jeeves-dev", False),
        # IRC nicks may contain []; brackets are literal, not char-class
        ("bot[1]", "bot[1]", True),
        ("bot[1]", "bot1", False),
        ("bot*", "bot[1]", True),
    ],
)
def test_ping_matches(pattern, nick, expect):
    assert ping_matches(pattern, nick) is expect


def test_pong_egress_allowed():
    assert shop_egress_allowed_for_chair("pong") is True


def test_handle_shop_ping_channel_and_pm(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})

    class FakeClient:
        def join(self, *a):
            pass

        def privmsg(self, *a, **k):
            pass

        def close(self):
            pass

        def wait_privmsg(self, timeout=0.3):
            return None

    chair = JeevesChair(
        "127.0.0.1",
        1,
        home,
        "http://127.0.0.1:9",
        nick="Jeeves",
        shops=["#flamingo"],
        client=FakeClient(),
    )
    chair._handle_shop("alice", "#flamingo", "ping")
    assert ("#flamingo", "pong") in chair.shop_egress
    assert "ping:alice:#flamingo" in chair.handled

    chair.shop_egress.clear()
    chair.handled.clear()
    chair._handle_shop("bob", "Jeeves", "ping jeeves")
    assert ("bob", "pong") in chair.pm_egress
    assert "ping:bob:bob" in chair.handled

    chair.pm_egress.clear()
    chair.handled.clear()
    chair._handle_shop("carol", "#flamingo", "ping bob-*")
    assert chair.shop_egress == []
    assert chair.pm_egress == []
    assert not any(h.startswith("ping:") for h in chair.handled)

    chair.nick = "Jeeves-dev"
    chair._handle_shop("dave", "#flamingo", "ping jeeves-*")
    assert ("#flamingo", "pong") in chair.shop_egress


def test_local_ircd_ping_channel_and_pm(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    save_queue(home, {"v": 1, "unaccepted": [], "accepted": [], "done": [], "workers": {}})
    with no_llm_network_guard():
        ircd = LocalIrcd()
        port = ircd.start()
        rx = StubReceiver(home)
        rport = rx.start()
        base = f"http://127.0.0.1:{rport}"
        chair = JeevesChair("127.0.0.1", port, home, base, nick="Jeeves", shops=["#bobiverse"])
        worker = IrcClient("127.0.0.1", port, "alice")
        worker.join("#bobiverse")
        chair.start()
        time.sleep(0.2)
        try:
            worker.privmsg("#bobiverse", "ping")
            msg = worker.wait_privmsg(
                predicate=lambda m: m[0].lower() == "jeeves"
                and m[1].lower() == "#bobiverse"
                and m[2].strip().lower() == "pong",
                timeout=5.0,
            )
            assert msg is not None

            worker.privmsg("Jeeves", "ping j*")
            msg2 = worker.wait_privmsg(
                predicate=lambda m: m[0].lower() == "jeeves"
                and m[1].lower() == "alice"
                and m[2].strip().lower() == "pong",
                timeout=5.0,
            )
            assert msg2 is not None

            # non-match: no handled ping entry added
            before_n = len([h for h in chair.handled if h.startswith("ping:")])
            worker.privmsg("#bobiverse", "ping bob-*")
            time.sleep(0.6)
            after_n = len([h for h in chair.handled if h.startswith("ping:")])
            assert after_n == before_n
        finally:
            chair.stop()
            worker.close()
            rx.stop()
            ircd.stop()
