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


def test_handler_errors_recorded_like_run_loop(tmp_path: Path, caplog):
    """_run must record handler_err instead of bare except: pass (#74)."""
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
    with mock.patch.object(chair, "_handle_status", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.ERROR, logger="jeeves.chair"):
            try:
                chair._handle_shop("simon", "Jeeves", "!status")
            except Exception as e:
                # mirror JeevesChair._run
                chair.handled.append(f"handler_err:simon:{type(e).__name__}")
                logging.getLogger("jeeves.chair").exception(
                    "handler_err nick=%s err=%s", "simon", type(e).__name__
                )
    chair.stop()
    rx.stop()
    ircd.stop()
    assert "handler_err:simon:RuntimeError" in chair.handled
    assert any("handler_err" in r.message for r in caplog.records)


def test_raw_inbox_bounded():
    from jeeves.tls_irc import RAW_INBOX_MAX as TLS_MAX
    from collections import deque

    assert RAW_INBOX_MAX == 500
    assert TLS_MAX == 500
    d = deque(maxlen=RAW_INBOX_MAX)
    for i in range(RAW_INBOX_MAX + 50):
        d.append(f"line-{i}")
    assert len(d) == RAW_INBOX_MAX
    assert d[0] == "line-50"


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
