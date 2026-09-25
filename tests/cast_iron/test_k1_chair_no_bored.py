"""K1 CAST IRON: chair never handles !BORED; ear owns offers.

FR #2 / brief K1 / agentic_irc #211.
Failing-test-first contract: these assertions define the gate outcome.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

from jeeves import cast_iron, roles, wire
from jeeves.cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    chair_may_post_claim_in_shop,
    is_forbidden_shop_egress,
    scan_source_for_forbidden_chair_handlers,
)
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


def test_k1_policy_flags_are_false():
    assert chair_handles_bored() is False
    assert chair_may_offer() is False
    assert chair_may_post_claim_in_shop() is False


def test_k1_forbidden_egress_detects_legacy_claim_lines():
    assert is_forbidden_shop_egress("flamingo-1: OFFER FR o/r#1 https://x")
    assert is_forbidden_shop_egress("NAK !BORED busy")
    assert is_forbidden_shop_egress("o/r FR #1")
    assert is_forbidden_shop_egress("no jobs")
    assert not is_forbidden_shop_egress("ACK FR o/r#1")  # inbound, not egress shape alone


def test_k1_source_scan_no_legacy_chair_bored_handlers():
    hits = scan_source_for_forbidden_chair_handlers()
    assert hits == [], f"legacy chair !bored path still present: {hits}"


def test_k1_jeeves_chair_source_has_no_offer_call():
    src = inspect.getsource(JeevesChair)
    assert "format_offer" not in src
    assert "top_unaccepted" not in src
    assert "claim_top(" not in src
    assert "_git_bored" not in src
    assert "git-claim" not in src


def test_k1_bob_ear_owns_bored_offer_path():
    src = inspect.getsource(BobEar)
    assert "is_bored" in src
    # K11: ear owns OFFER via EarOfferState (not chair; not multi-line ASSIGN)
    assert "EarOfferState" in src or "offer_state" in src
    assert "OFFER" in src or "offer" in src


@pytest.mark.parametrize(
    "body",
    ["!bored", "!BORED", "!Bored", "!!bored", "! bored"],
)
def test_k1_bored_grammar(body: str):
    assert wire.is_bored(body)


def test_k1_live_irc_jeeves_silent_on_bored_ear_offers(tmp_path: Path):
    """Integration: !BORED in shop → ear OFFER; Jeeves zero shop claim posts."""
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
                    "id": "#2",
                    "seq": 1,
                    "line": "K1",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/2",
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

    jeeves = JeevesChair("127.0.0.1", port, home, base, shops=["#flamingo"])
    ear = BobEar("127.0.0.1", port, home, machine="flamingo")
    worker = IrcClient("127.0.0.1", port, "flamingo-42")
    worker.join("#flamingo")
    # sniffer: watch shop for any line from Jeeves
    sniffer = IrcClient("127.0.0.1", port, "sniff-1")
    sniffer.join("#flamingo")

    jeeves.start()
    ear.start()
    time.sleep(0.15)
    try:
        worker.privmsg("#flamingo", "!BORED")
        offer = worker.wait_privmsg(
            predicate=lambda m: m[0].startswith("bob-") and "OFFER" in m[2],
            timeout=5.0,
        )
        assert offer is not None
        assert "OFFER FR SimonBarnett/gh-Jeeves#2" in offer[2]
        assert offer[2].startswith("flamingo-42:")

        # drain sniffer briefly
        time.sleep(0.4)
        sniffer.wait_privmsg(timeout=0.2)
        jeeves_shop = [
            m for m in sniffer.inbox if m[0].lower() == "jeeves" and m[1].lower() == "#flamingo"
        ]
        assert jeeves_shop == [], f"Jeeves posted in shop: {jeeves_shop}"

        assert "ignored_bored" in jeeves.handled
        assert jeeves.shop_egress == []
        assert not any("OFFER" in h for h in jeeves.handled)
        assert not any(h.startswith("offer") for h in jeeves.handled)
        # ear did the work
        assert ear.offers, "ear must emit OFFER"
    finally:
        jeeves.stop()
        ear.stop()
        worker.close()
        sniffer.close()
        rx.stop()
        ircd.stop()


def test_k1_chair_blocks_forced_shop_claim_egress(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    try:
        j = JeevesChair("127.0.0.1", port, home, f"http://127.0.0.1:{rport}")
        j._shop_privmsg("#flamingo", "flamingo-1: OFFER FR o/r#1 http://x")
        j._shop_privmsg("#flamingo", "NAK !BORED wait")
        assert j.shop_egress == []
        assert any(h.startswith("blocked_shop_egress") for h in j.handled)
        j.stop()
    finally:
        rx.stop()
        ircd.stop()
