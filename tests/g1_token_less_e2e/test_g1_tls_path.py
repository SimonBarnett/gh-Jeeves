"""G1 TLS path (FR #46): local TLS test ircd + native TlsIrcClient (no agentic_irc)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jeeves.backoff import is_throttle_error, throttle_delay_s
from jeeves.guard import no_llm_network_guard
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair
from jeeves.tls_irc import (
    LocalTlsIrcd,
    TlsIrcClient,
    assert_no_agentic_irc_import,
    make_self_signed_cert,
)


def test_no_agentic_irc_import_in_package():
    hits = assert_no_agentic_irc_import()
    assert hits == [], hits


def test_throttle_backoff_grows():
    assert is_throttle_error("ERROR :Closing Link: too many connections")
    d1 = throttle_delay_s(1, base=2.0, cap=300.0, jitter=0.0)
    d3 = throttle_delay_s(3, base=2.0, cap=300.0, jitter=0.0)
    assert d1 == 2.0
    assert d3 == 8.0
    assert throttle_delay_s(20, base=2.0, cap=10.0, jitter=0.0) == 10.0


def test_g1_tls_chair_ack_path(tmp_path: Path):
    """TLS front + plain local ircd; JeevesChair over TlsIrcClient insecure."""
    cert_dir = tmp_path / "certs"
    cert, key = make_self_signed_cert(cert_dir)
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
                    "id": "#46",
                    "seq": 1,
                    "line": "TLS",
                    "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/46",
                }
            ],
            "accepted": [],
            "done": [],
            "workers": {},
        },
    )

    with no_llm_network_guard():
        tlsd = LocalTlsIrcd(certfile=str(cert), keyfile=str(key))
        port = tlsd.start()
        rx = StubReceiver(home)
        rport = rx.start()
        base = f"http://127.0.0.1:{rport}"

        client = TlsIrcClient(
            "127.0.0.1",
            port,
            "Jeeves",
            tls=True,
            insecure=True,
            flood_s=0.05,
        )
        chair = JeevesChair(
            "127.0.0.1",
            port,
            home,
            base,
            nick="Jeeves",
            shops=["#flamingo"],
            client=client,
        )
        worker = TlsIrcClient(
            "127.0.0.1",
            port,
            "flamingo-46",
            tls=True,
            insecure=True,
            flood_s=0.05,
        )
        worker.join("#flamingo")
        chair.start()
        time.sleep(0.2)
        try:
            worker.privmsg("#flamingo", "ACK FR SimonBarnett/gh-Jeeves#46")
            deadline = time.time() + 8
            while time.time() < deadline and not any(
                h.startswith("ack:flamingo-46:") for h in chair.handled
            ):
                time.sleep(0.05)
            assert any(
                "ack:flamingo-46:SimonBarnett/gh-Jeeves#46" in h for h in chair.handled
            ), chair.handled
        finally:
            chair.stop()
            worker.close()
            rx.stop()
            tlsd.stop()
