"""FR #110: QUIT must not immediately drop accepted claims (reconnect grace)."""
from __future__ import annotations

import time
from pathlib import Path

from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import Claim, accept_job, apply_queue_event, load_queue
from jeeves.roles import JeevesChair, DEFAULT_QUIT_REJOIN_GRACE_S


def _seed_and_ack(home: Path, nick: str = "marchhare-31712") -> None:
    apply_queue_event(
        home,
        Claim(
            "SimonBarnett/gh-Jeeves",
            "FR",
            "#7",
            "issues",
            "opened",
            "x",
            url="https://github.com/SimonBarnett/gh-Jeeves/issues/7",
        ),
    )
    st, _ = accept_job(home, nick, "#marchhare", "FR", "SimonBarnett/gh-Jeeves", 7)
    assert st == "accepted"


def test_quit_then_rejoin_within_grace_keeps_claim(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-31712"
    _seed_and_ack(home, nick)
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair(
            "127.0.0.1",
            port,
            home,
            "http://127.0.0.1:9",
            client=client,
            shops=["#marchhare"],
            quit_rejoin_grace_s=120.0,
        )
        assert len(load_queue(home)["accepted"]) == 1
        chair._handle_quit_raw(f":{nick}!u@h QUIT :Client Quit")
        assert any(h.startswith(f"quit_hold:{nick}") for h in chair.handled)
        doc = load_queue(home)
        assert len(doc["accepted"]) == 1
        assert nick in (doc.get("workers") or {})
        # Rejoin within grace
        chair._handle_join_raw(f":{nick}!u@h JOIN #marchhare")
        assert any(h.startswith(f"quit_rejoin:{nick}") for h in chair.handled)
        doc2 = load_queue(home)
        assert len(doc2["accepted"]) == 1
        assert str(doc2["accepted"][0].get("nick")) == nick
        assert nick in (doc2.get("workers") or {})
        # Flush should not release
        chair._flush_quit_holds(now=time.time() + 200)
        assert len(load_queue(home)["accepted"]) == 1
    finally:
        client.close()
        ircd.stop()


def test_quit_without_rejoin_releases_after_grace(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    nick = "marchhare-31712"
    _seed_and_ack(home, nick)
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair(
            "127.0.0.1",
            port,
            home,
            "http://127.0.0.1:9",
            client=client,
            shops=["#marchhare"],
            quit_rejoin_grace_s=2.0,
            auto_join=False,
        )
        t0 = time.time()
        chair._handle_quit_raw(f":{nick}!u@h QUIT :ping timeout")
        assert len(load_queue(home)["accepted"]) == 1
        # Before grace
        chair._flush_quit_holds(now=t0 + 0.5)
        assert len(load_queue(home)["accepted"]) == 1
        # After grace
        chair._flush_quit_holds(now=t0 + 3.0)
        assert any(h.startswith(f"quit_release:{nick}") for h in chair.handled)
        doc = load_queue(home)
        assert len(doc["accepted"]) == 0
        assert nick not in (doc.get("workers") or {})
        assert any(r.get("id") == "#7" for r in doc["unaccepted"])
    finally:
        client.close()
        ircd.stop()


def test_default_grace_is_at_least_120():
    assert DEFAULT_QUIT_REJOIN_GRACE_S >= 120.0
