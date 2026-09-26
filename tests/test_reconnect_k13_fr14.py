"""FR #14 / K13: reconnect in place with exponential backoff on throttle.

Seed outcome: Reconnect in place with exponential backoff on throttle ERROR;
Jeeves (and ears) get the same. Evidence: PART :recv idle → relaunch storm →
Ergo "too many connections"; also #108 — one failed reconnect then stall.

Failing-test-first: encode forever-retry + capped backoff + recover when
server returns, before the client stops after one refused connect.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from jeeves.backoff import is_throttle_error, throttle_delay_s
from jeeves.tls_irc import TlsIrcClient


def test_throttle_error_matches_ergo_too_many_connections():
    assert is_throttle_error("ERROR :Closing Link: (too many connections)")
    assert is_throttle_error("ERROR :Closing Link: too many connections")
    assert is_throttle_error(":irc.ntsa.uk  ERROR :Try again later, throttled")
    assert not is_throttle_error("ERROR :Nickname is already in use")


def test_backoff_caps_near_30s_for_reconnect_storm():
    """K13 / #108: cap ~30s so storm waits, not multi-minute silence."""
    d = throttle_delay_s(20, base=2.0, cap=30.0, jitter=0.0)
    assert d == 30.0
    d1 = throttle_delay_s(1, base=2.0, cap=30.0, jitter=0.0)
    d2 = throttle_delay_s(2, base=2.0, cap=30.0, jitter=0.0)
    d3 = throttle_delay_s(3, base=2.0, cap=30.0, jitter=0.0)
    assert d1 == 2.0 and d2 == 4.0 and d3 == 8.0


def _bare_client() -> TlsIrcClient:
    c = object.__new__(TlsIrcClient)
    c.host = "127.0.0.1"
    c.port = 1
    c.nick = "Jeeves"
    c.tls = False
    c.insecure = True
    c.cafile = None
    c.cert_pin_sha256 = None
    c.password = ""
    c.sasl_user = ""
    c.sasl_password = ""
    c.flood_s = 0.0
    c.connect_timeout = 0.5
    c.sock = None
    c.buf = ""
    c.inbox = []
    c.raw_inbox = __import__("collections").deque(maxlen=50)
    c.on_raw = None
    c._lock = threading.Lock()
    c.reconnect_count = 0
    c.last_throttle = False
    return c


def test_reconnect_retries_after_refused_then_recovers(monkeypatch):
    """#108 shape: Ergo down → refused; then up → register. Must not stall."""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(float(s)))
    c = _bare_client()
    c.last_throttle = True
    calls = {"n": 0}

    def fake_connect() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionRefusedError("ergo down")
        c.sock = object()  # pretend connected

    c._connect_and_register = fake_connect  # type: ignore[method-assign]

    # First two attempts fail; ensure_connected / reconnect_loop must keep going.
    with pytest.raises(OSError):
        c.reconnect(attempt=1)
    assert c.sock is None
    with pytest.raises(OSError):
        c.reconnect(attempt=2)
    assert c.sock is None

    delay = c.reconnect(attempt=3)
    assert c.sock is not None
    assert calls["n"] == 3
    assert delay >= 0.5
    # success clears throttle storm counter
    assert c.last_throttle is False
    assert c.reconnect_count == 0


def test_wait_privmsg_recovers_when_sock_none_then_server_up(monkeypatch):
    """Chair poll must reconnect when sock is None (not silent-idle forever)."""
    c = _bare_client()
    c.sock = None
    calls = {"n": 0}
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(float(s)))

    def fake_connect() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError("still down")
        c.sock = object()

    c._connect_and_register = fake_connect  # type: ignore[method-assign]

    # Patch recv path: once sock exists, timeout like idle poll
    class _FakeSock:
        def recv(self, _n: int) -> bytes:
            raise socket.timeout()

        def close(self) -> None:
            pass

        def settimeout(self, _t: float) -> None:
            pass

        def sendall(self, _b: bytes) -> None:
            pass

    real_connect = fake_connect

    def connect_then_fakesock() -> None:
        real_connect()
        if c.sock is not None and not isinstance(c.sock, _FakeSock):
            c.sock = _FakeSock()

    c._connect_and_register = connect_then_fakesock  # type: ignore[method-assign]

    # Must attempt reconnect inside drain (sock was None)
    c.wait_privmsg(timeout=2.0)
    assert calls["n"] >= 2
    assert c.sock is not None


def test_successful_reconnect_resets_throttle_flag(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    c = _bare_client()
    c.last_throttle = True
    c.reconnect_count = 5

    def ok() -> None:
        c.sock = object()

    c._connect_and_register = ok  # type: ignore[method-assign]
    c.reconnect(attempt=6)
    assert c.last_throttle is False
    assert c.reconnect_count == 0
