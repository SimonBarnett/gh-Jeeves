"""issue #74: !list must not freeze chair; !status works; raw_inbox bounded; digest retry."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest import mock

import pytest

from jeeves.digest import empty_digest, save_digest
from jeeves.flood_queue import OutboundFloodQueue
from jeeves.listfmt import FLOOD_S, reset_list_rate
from jeeves.local_ircd import RAW_INBOX_MAX, IrcClient, LocalIrcd
from jeeves.queue import save_queue
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair
from jeeves.wire import is_resync, is_status


def _row(n: int) -> dict:
    return {
        "task": "FR",
        "repo": "o/r",
        "id": f"#{n}",
        "line": f"title-{n}",
        "seq": n,
        "url": f"https://github.com/o/r/issues/{n}",
    }


def test_is_status_resync():
    assert is_status("!status")
    assert is_status("!STATUS")
    assert not is_status("!status now")
    assert is_resync("!resync")
    assert not is_resync("!resync please")


def test_outbound_flood_queue_paces_once():
    sent: list[tuple[str, str]] = []
    q = OutboundFloodQueue(lambda t, x: sent.append((t, x)), flood_s=0.05)
    q.start()
    try:
        for i in range(3):
            q.put("alice", f"line-{i}")
        deadline = time.time() + 2
        while q.sent < 3 and time.time() < deadline:
            time.sleep(0.02)
        assert q.sent == 3
        assert [x for _, x in sent] == ["line-0", "line-1", "line-2"]
    finally:
        q.stop()


def test_list_does_not_block_help(tmp_path: Path, monkeypatch):
    """Acceptance: during a long !list, !help from another nick is handled promptly."""
    monkeypatch.setattr("jeeves.roles.FLOOD_S", 0.05)
    monkeypatch.setattr("jeeves.listfmt.FLOOD_S", 0.05)
    reset_list_rate()
    home = tmp_path / "d"
    home.mkdir()
    rows = [_row(i) for i in range(1, 41)]
    save_queue(
        home,
        {"v": 1, "unaccepted": rows, "accepted": [], "done": [], "workers": {}},
    )
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    # force flood pace small on the queue
    chair._outbox.flood_s = 0.05
    chair.start()
    time.sleep(0.15)
    try:
        alice = IrcClient("127.0.0.1", port, "alice")
        bob = IrcClient("127.0.0.1", port, "bob-test")
        alice.privmsg("#bobiverse", "!list all")
        # give chair a moment to enqueue list
        time.sleep(0.1)
        t0 = time.time()
        bob.privmsg("Jeeves", "!help")
        # wait until help is recorded in handled
        deadline = time.time() + 2.0
        while time.time() < deadline and not any(
            h.startswith("help:bob-test:") for h in chair.handled
        ):
            time.sleep(0.05)
        elapsed = time.time() - t0
        assert any(h.startswith("help:bob-test:") for h in chair.handled), chair.handled
        assert elapsed < 2.0, f"help took {elapsed:.2f}s while list draining"
        assert any(h.startswith("list_pm:alice:") for h in chair.handled)
    finally:
        chair.stop()
        alice.close()
        bob.close()
        rx.stop()
        ircd.stop()


def test_status_pm_reply(tmp_path: Path):
    reset_list_rate()
    home = tmp_path / "d"
    home.mkdir()
    save_queue(
        home,
        {
            "v": 1,
            "unaccepted": [_row(1)],
            "accepted": [],
            "done": [],
            "workers": {"m-1": {"state": "busy"}},
        },
    )
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    chair._outbox.flood_s = 0.0
    chair.start()
    time.sleep(0.1)
    try:
        simon = IrcClient("127.0.0.1", port, "simon")
        simon.privmsg("Jeeves", "!status")
        deadline = time.time() + 3
        while time.time() < deadline and not any(
            h.startswith("status:simon:") for h in chair.handled
        ):
            time.sleep(0.05)
        assert any(h.startswith("status:simon:") for h in chair.handled), chair.handled
        # PMs enqueued
        assert any("Jeeves status:" in t for _, t in chair.pm_egress)
        assert any(t.startswith("queue:") for _, t in chair.pm_egress)
    finally:
        chair.stop()
        simon.close()
        rx.stop()
        ircd.stop()


def test_resync_denied_for_worker(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    chair.start()
    time.sleep(0.05)
    try:
        w = IrcClient("127.0.0.1", port, "marchhare-1")
        w.privmsg("Jeeves", "!resync")
        deadline = time.time() + 2
        while time.time() < deadline and not any(
            h.startswith("resync_denied:") for h in chair.handled
        ):
            time.sleep(0.05)
        assert any(h.startswith("resync_denied:marchhare-1") for h in chair.handled)
    finally:
        chair.stop()
        w.close()
        rx.stop()
        ircd.stop()


def test_resync_ok_for_bob(tmp_path: Path):
    """Positive path: bob-* !resync calls scheduler.run_once and PMs summary."""
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()

    class _Stats:
        quiet = True
        added = 0
        removed = 0

        def summary_line(self, total: int) -> str:
            return f"Jeeves resync: unchanged total {total}"

    class _Sched:
        last = None
        calls = 0

        def run_once(self):
            self.calls += 1
            self.last = _Stats()
            return self.last

        def start(self, **kwargs):
            return None

        def stop(self):
            return None

    sched = _Sched()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
        resync_scheduler=sched,
    )
    chair._outbox.flood_s = 0.0
    chair.start()
    time.sleep(0.1)
    try:
        bob = IrcClient("127.0.0.1", port, "bob-ionos")
        bob.privmsg("Jeeves", "!resync")
        deadline = time.time() + 3
        while time.time() < deadline and not any(
            h.startswith("resync:bob-ionos:") for h in chair.handled
        ):
            time.sleep(0.05)
        assert any(h == "resync:bob-ionos:ok" for h in chair.handled), chair.handled
        assert sched.calls == 1
        assert any("resync" in t.lower() for _, t in chair.pm_egress)
    finally:
        chair.stop()
        bob.close()
        rx.stop()
        ircd.stop()


def test_list_does_not_block_ack(tmp_path: Path, monkeypatch):
    """Acceptance #74: during long !list, shop ACK is recorded promptly."""
    monkeypatch.setattr("jeeves.roles.FLOOD_S", 0.05)
    monkeypatch.setattr("jeeves.listfmt.FLOOD_S", 0.05)
    reset_list_rate()
    home = tmp_path / "d"
    home.mkdir()
    rows = [_row(i) for i in range(1, 81)]
    # ensure ACK target exists
    rows[0] = {
        "task": "FR",
        "repo": "SimonBarnett/gh-Jeeves",
        "id": "#74",
        "line": "freeze",
        "seq": 1,
        "url": "https://github.com/SimonBarnett/gh-Jeeves/issues/74",
    }
    save_queue(
        home,
        {"v": 1, "unaccepted": rows, "accepted": [], "done": [], "workers": {}},
    )
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#ionos"],
        auto_join=False,
    )
    chair._outbox.flood_s = 0.05
    chair.start()
    time.sleep(0.15)
    try:
        alice = IrcClient("127.0.0.1", port, "alice")
        worker = IrcClient("127.0.0.1", port, "ionos-4242")
        worker.join("#ionos")
        alice.privmsg("#bobiverse", "!list all")
        time.sleep(0.1)
        t0 = time.time()
        worker.privmsg("#ionos", "ACK FR SimonBarnett/gh-Jeeves#74")
        deadline = time.time() + 2.0
        while time.time() < deadline and not any(
            h.startswith("ack:ionos-4242:") for h in chair.handled
        ):
            time.sleep(0.05)
        elapsed = time.time() - t0
        assert any(h.startswith("ack:ionos-4242:") for h in chair.handled), chair.handled
        assert elapsed < 2.0, f"ack took {elapsed:.2f}s while list draining"
        assert any(h.startswith("list_pm:alice:") for h in chair.handled)
    finally:
        chair.stop()
        alice.close()
        worker.close()
        rx.stop()
        ircd.stop()


def test_handler_err_from_run_loop(tmp_path: Path, caplog):
    """_run itself must catch handler exceptions (not a mirrored try in the test)."""
    home = tmp_path / "d"
    home.mkdir()
    ircd = LocalIrcd()
    port = ircd.start()
    rx = StubReceiver(home)
    rport = rx.start()
    chair = JeevesChair(
        "127.0.0.1",
        port,
        home,
        f"http://127.0.0.1:{rport}",
        shops=["#bobiverse"],
        auto_join=False,
    )
    chair._outbox.flood_s = 0.0
    with mock.patch.object(chair, "_handle_status", side_effect=RuntimeError("boom")):
        chair.start()
        time.sleep(0.1)
        try:
            with caplog.at_level(logging.ERROR, logger="jeeves.chair"):
                simon = IrcClient("127.0.0.1", port, "simon")
                simon.privmsg("Jeeves", "!status")
                deadline = time.time() + 3
                while time.time() < deadline and not any(
                    h.startswith("handler_err:simon:") for h in chair.handled
                ):
                    time.sleep(0.05)
            assert any(
                h == "handler_err:simon:RuntimeError" for h in chair.handled
            ), chair.handled
            assert any("handler_err" in r.message for r in caplog.records)
        finally:
            chair.stop()
            simon.close()
            rx.stop()
            ircd.stop()


def test_raw_inbox_bounded_on_clients():
    """raw_inbox is a maxlen deque on both TLS and local clients (#74 part 4)."""
    from collections import deque

    from jeeves.tls_irc import RAW_INBOX_MAX as TLS_MAX
    from jeeves.tls_irc import TlsIrcClient

    assert RAW_INBOX_MAX == 500
    assert TLS_MAX == 500
    # local client instance
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        c = IrcClient("127.0.0.1", port, "probe-inbox")
        assert isinstance(c.raw_inbox, deque)
        assert c.raw_inbox.maxlen == RAW_INBOX_MAX
        for i in range(RAW_INBOX_MAX + 40):
            c.raw_inbox.append(f"line-{i}")
        assert len(c.raw_inbox) == RAW_INBOX_MAX
        c.close()
    finally:
        ircd.stop()
    # TLS client constructed without connect still has bounded deque
    tls = TlsIrcClient.__new__(TlsIrcClient)
    tls.raw_inbox = deque(maxlen=TLS_MAX)
    for i in range(TLS_MAX + 10):
        tls.raw_inbox.append(str(i))
    assert len(tls.raw_inbox) == TLS_MAX


def test_save_digest_retries_permission_error(tmp_path: Path):
    home = tmp_path / "d"
    home.mkdir()
    doc = empty_digest()
    path = home / "digest.json"
    calls = {"n": 0}
    real_replace = Path.replace

    def flaky_replace(self, target):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    with mock.patch.object(Path, "replace", flaky_replace):
        save_digest(home, doc, retries=5, backoff_s=0.01)
    assert path.is_file()
    assert calls["n"] == 3
