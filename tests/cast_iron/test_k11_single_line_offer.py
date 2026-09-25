"""K11: ASSIGN has no owner — deterministic single-line OFFER from ear.

FR #12 / brief K11. One OFFER per worker, busy-gated; never multi-line wakes.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.length_safe import wire_line_bytes
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.offer import (
    EarOfferState,
    contains_assign,
    format_single_line_offer,
    is_single_line,
    parse_offer,
    split_would_multi_wake,
)
from jeeves.queue import save_queue, set_worker_state
from jeeves.receiver import StubReceiver
from jeeves.roles import BobEar, JeevesChair


def _job(n: int = 12) -> dict:
    return {
        "repo": "SimonBarnett/gh-Jeeves",
        "task": "FR",
        "id": f"#{n}",
        "seq": n,
        "line": "K11 title with spaces",
        "url": f"https://github.com/SimonBarnett/gh-Jeeves/issues/{n}",
    }


def test_k11_offer_is_single_line_no_assign():
    offer = format_single_line_offer("flamingo-1", _job())
    assert is_single_line(offer.text)
    assert not contains_assign(offer.text)
    assert "\n" not in offer.text and "\r" not in offer.text
    assert offer.text.startswith("flamingo-1: OFFER FR ")
    assert "SimonBarnett/gh-Jeeves#12" in offer.text
    parsed = parse_offer(offer.text)
    assert parsed is not None
    assert parsed.task == "FR"
    assert parsed.number == "12"
    assert wire_line_bytes(offer.text, nick="bob-flamingo", channel="#flamingo") <= 512


def test_k11_multiline_title_collapsed():
    row = _job()
    row["url"] = "https://github.com/SimonBarnett/gh-Jeeves/issues/12"
    # inject CR/LF into fields that a naive formatter might paste
    row["line"] = "line1\nline2\rline3"
    offer = format_single_line_offer("flamingo-1", row)
    assert is_single_line(offer.text)
    assert not split_would_multi_wake(offer.text)


def test_k11_split_would_multi_wake_detects_assign_stack():
    bad = "nick: ASSIGN FR a/b#1 http://x\nnick: ASSIGN FR a/b#2 http://y"
    assert split_would_multi_wake(bad)
    assert contains_assign(bad)
    good = "flamingo-1: OFFER FR a/b#1 https://github.com/a/b/issues/1"
    assert not split_would_multi_wake(good)


def test_k11_one_offer_per_worker_and_busy_gate(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [_job(1), _job(2)],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )
    st = EarOfferState()
    d1 = st.decide(home, "flamingo-9", machine="flamingo")
    assert d1.action == "offer"
    assert d1.line and is_single_line(d1.line)
    d2 = st.decide(home, "flamingo-9", machine="flamingo")
    assert d2.action == "nak_open"  # second !bored while open — no stack
    st.on_worker_ack("flamingo-9")
    d3 = st.decide(home, "flamingo-9", machine="flamingo")
    assert d3.action == "offer"

    set_worker_state(home, "flamingo-9", "busy")
    st2 = EarOfferState()
    db = st2.decide(home, "flamingo-9", machine="flamingo")
    assert db.action == "nak_busy"
    assert db.line and "NAK busy" in db.line


def test_k11_ear_bored_path_retired_fr106(tmp_path: Path):
    """FR #106: ear no longer OFFERs on !bored — Jeeves assigns instead."""
    home = tmp_path / "digest"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [_job(12), _job(13)],
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
    jeeves.live_seats_override = {"flamingo-12"}
    ear = BobEar("127.0.0.1", port, home, machine="flamingo")
    worker = IrcClient("127.0.0.1", port, "flamingo-12")
    worker.join("#flamingo")
    jeeves.start()
    ear.start()
    time.sleep(0.15)
    try:
        worker.privmsg("#flamingo", "!bored")
        assign = worker.wait_privmsg(
            predicate=lambda m: m[0].lower() == "jeeves"
            and m[2].startswith("flamingo-12:")
            and "OFFER" not in m[2],
            timeout=5.0,
        )
        assert assign is not None
        assert is_single_line(assign[2])
        assert not contains_assign(assign[2])
        assert "OFFER" not in assign[2]
        assert any(str(x).startswith("retired_bored:") for x in ear.offers)
        assert not any("OFFER" in str(x) for x in ear.offers)
    finally:
        jeeves.stop()
        ear.stop()
        worker.close()
        rx.stop()
        ircd.stop()


def test_k11_chair_does_not_use_legacy_offer_formatter():
    import inspect
    from jeeves.roles import JeevesChair

    src = inspect.getsource(JeevesChair)
    assert "format_offer(" not in src
    assert "format_single_line_offer" not in src
