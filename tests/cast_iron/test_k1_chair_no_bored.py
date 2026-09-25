"""K1 CAST IRON rewritten for FR #106: Jeeves assigns on !bored (ear OFFER retired).

Former rule (chair never !bored) is reversed. Legacy OFFER/git-claim remain forbidden.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

from jeeves import wire
from jeeves.cast_iron import (
    chair_handles_bored,
    chair_may_offer,
    chair_may_post_claim_in_shop,
    is_forbidden_shop_egress,
    scan_source_for_forbidden_chair_handlers,
    shop_egress_allowed_for_chair,
)
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


def test_k1_policy_flags_chair_owns_bored():
    assert chair_handles_bored() is True
    assert chair_may_offer() is True
    assert chair_may_post_claim_in_shop() is True


def test_k1_forbidden_egress_legacy_offer_still_blocked():
    assert is_forbidden_shop_egress("flamingo-1: OFFER FR o/r#1 https://x")
    assert is_forbidden_shop_egress("NAK !BORED busy")
    assert is_forbidden_shop_egress("o/r FR #1")
    assert is_forbidden_shop_egress("no jobs")
    # FR #106 assign lines are allowed
    assert not is_forbidden_shop_egress(
        "flamingo-1: FR SimonBarnett/gh-Jeeves#2 https://github.com/SimonBarnett/gh-Jeeves/issues/2"
    )
    assert not is_forbidden_shop_egress("flamingo-1: nothing queued")
    assert shop_egress_allowed_for_chair("flamingo-1: nothing queued")


def test_k1_source_scan_no_legacy_chair_claim_handlers():
    hits = scan_source_for_forbidden_chair_handlers()
    assert hits == [], f"legacy chair claim path still present: {hits}"


def test_k1_jeeves_chair_has_no_legacy_offer_helpers():
    src = inspect.getsource(JeevesChair)
    assert "format_single_line_offer" not in src
    assert "_git_bored" not in src
    assert "git-claim" not in src
    assert "assign_state" in src or "_handle_bored_assign" in src


def test_k1_bob_ear_retired_bored_offer():
    src = inspect.getsource(BobEar)
    assert "retired_bored" in src or "FR #106" in src or "retired" in src.lower()


@pytest.mark.parametrize(
    "body",
    ["!bored", "!BORED", "!Bored", "!!bored", "! bored"],
)
def test_k1_bored_grammar(body: str):
    assert wire.is_bored(body)


def test_k1_live_irc_jeeves_assigns_on_bored(tmp_path: Path):
    """Integration: !bored in shop → Jeeves assign line; ear does not OFFER."""
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
    jeeves.live_seats_override = {"flamingo-42"}
    ear = BobEar("127.0.0.1", port, home, machine="flamingo")
    worker = IrcClient("127.0.0.1", port, "flamingo-42")
    worker.join("#flamingo")

    jeeves.start()
    ear.start()
    time.sleep(0.15)
    try:
        worker.privmsg("#flamingo", "!BORED")
        assign = worker.wait_privmsg(
            predicate=lambda m: m[0].lower() == "jeeves"
            and m[2].startswith("flamingo-42:")
            and "OFFER" not in m[2],
            timeout=5.0,
        )
        assert assign is not None
        assert "FR SimonBarnett/gh-Jeeves#2" in assign[2]
        assert "OFFER" not in assign[2]
        assert any(h.startswith("assign:flamingo-42:") for h in jeeves.handled)
        # ear must not emit OFFER
        assert not any("OFFER" in x for x in ear.offers)
        assert any(str(x).startswith("retired_bored:") for x in ear.offers)
    finally:
        jeeves.stop()
        ear.stop()
        worker.close()
        rx.stop()
        ircd.stop()


def test_k1_chair_blocks_legacy_offer_egress(tmp_path: Path):
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
