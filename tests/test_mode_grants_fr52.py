"""FR #52: +h bob-* / +o simon by services account; workers/imposters get nothing."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.mode_grants import (
    ModeGrantController,
    Presence,
    already_has_mode,
    desired_mode,
    is_worker_style,
)
from jeeves.receiver import StubReceiver
from jeeves.roles import JeevesChair


def test_desired_mode_matrix():
    assert desired_mode(Presence("bob-flamingo", "bob-flamingo", "x", "#bobiverse")) == "h"
    assert desired_mode(Presence("bob-flamingo", "bob-flamingo", "x", "#flamingo")) == "o"
    assert desired_mode(Presence("bob-flamingo", None, "x", "#bobiverse")) is None
    assert (
        desired_mode(
            Presence("simon", "simon", "user.flamingo.irc.ntsa.uk", "#bobiverse")
        )
        == "o"
    )
    assert desired_mode(Presence("simon", "simon", "evil.example", "#bobiverse")) is None
    assert desired_mode(Presence("simon", None, "flamingo.host", "#bobiverse")) is None
    assert desired_mode(Presence("flamingo-123", "flamingo-123", "x", "#flamingo")) is None
    assert is_worker_style("marchhare-34992")


def test_idempotent_already_has():
    assert already_has_mode("o", "h")
    assert already_has_mode("h", "h")
    assert not already_has_mode("", "h")


def test_bob_join_gets_halfop(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    jeeves = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = ModeGrantController(jeeves, rate_s=0.0)
    jeeves.on_raw = ctrl.handle_raw
    jeeves.join("#bobiverse")
    time.sleep(0.05)

    bob = IrcClient("127.0.0.1", port, "bob-flamingo")
    bob.set_account("bob-flamingo")
    bob.set_host("ear.flamingo.local")
    bob.join("#bobiverse")
    # drain
    deadline = time.time() + 3
    while time.time() < deadline and not ctrl.state.mode_sent:
        jeeves._drain_until(lambda: False, timeout=0.2)
        time.sleep(0.05)
    assert any("+h bob-flamingo" in m for m in ctrl.state.mode_sent), ctrl.state.mode_sent
    # no channel PRIVMSG from grants
    assert not any("PRIVMSG #bobiverse" in r and "grant" in r.lower() for r in jeeves.raw_inbox)
    bob.close()
    jeeves.close()
    ircd.stop()


def test_simon_authed_fleet_gets_op(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    jeeves = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = ModeGrantController(jeeves, rate_s=0.0)
    jeeves.on_raw = ctrl.handle_raw
    jeeves.join("#bobiverse")
    simon = IrcClient("127.0.0.1", port, "simon")
    simon.set_account("simon")
    simon.set_host("session.marchhare.irc.ntsa.uk")
    simon.join("#bobiverse")
    deadline = time.time() + 3
    while time.time() < deadline and not any("+o simon" in m for m in ctrl.state.mode_sent):
        jeeves._drain_until(lambda: False, timeout=0.2)
    assert any("+o simon" in m for m in ctrl.state.mode_sent), ctrl.state.mode_sent
    simon.close()
    jeeves.close()
    ircd.stop()


def test_unauth_imposter_gets_nothing(tmp_path: Path):
    ircd = LocalIrcd()
    port = ircd.start()
    jeeves = IrcClient("127.0.0.1", port, "Jeeves")
    ctrl = ModeGrantController(jeeves, rate_s=0.0)
    jeeves.on_raw = ctrl.handle_raw
    jeeves.join("#bobiverse")
    fake = IrcClient("127.0.0.1", port, "simon")
    # no ACCOUNT
    fake.set_host("flamingo.host")
    fake.join("#bobiverse")
    deadline = time.time() + 2
    while time.time() < deadline:
        jeeves._drain_until(lambda: False, timeout=0.2)
        time.sleep(0.05)
        if any("unauth_skip:simon" in e for e in ctrl.state.events):
            break
    assert not any("+o simon" in m for m in ctrl.state.mode_sent)
    assert any("unauth_skip:simon" in e for e in ctrl.state.events)
    fake.close()
    jeeves.close()
    ircd.stop()


def test_worker_gets_nothing(tmp_path: Path):
    ctrl = ModeGrantController(_Raw(), rate_s=0.0)
    g = ctrl.on_join("flamingo-99", "#flamingo", account="flamingo-99", host="x")
    assert g is None
    assert ctrl.state.mode_sent == []


class _Raw:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def send_raw(self, line: str) -> None:
        self.lines.append(line)


def test_rejoin_sweep_idempotent():
    client = _Raw()
    ctrl = ModeGrantController(client, rate_s=0.0)
    ctrl.set_account("bob-ionos", "bob-ionos")
    ctrl.set_host("bob-ionos", "ionos.host")
    g1 = ctrl.maybe_grant("bob-ionos", "#bobiverse")
    assert g1 == "h"
    assert len(client.lines) == 1
    g2 = ctrl.maybe_grant("bob-ionos", "#bobiverse")
    assert g2 is None  # already has
    assert len(client.lines) == 1
    # sweep same
    ctrl.sweep_channel("#bobiverse", ["bob-ionos", "Jeeves"])
    assert len(client.lines) == 1


def test_chair_wires_mode_grants_on_bob_join(tmp_path: Path):
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
    )
    assert chair.mode_grants is not None
    chair.mode_grants.rate_s = 0.0
    chair.start()
    time.sleep(0.1)
    bob = IrcClient("127.0.0.1", port, "bob-flamingo")
    bob.set_account("bob-flamingo")
    bob.set_host("flamingo")
    bob.join("#bobiverse")
    deadline = time.time() + 3
    while time.time() < deadline and not chair.mode_grants.state.mode_sent:
        time.sleep(0.05)
    assert any("+h bob-flamingo" in m for m in chair.mode_grants.state.mode_sent)
    # channel silence: no OFFER/claim from mode path
    assert chair.shop_egress == []
    chair.stop()
    bob.close()
    rx.stop()
    ircd.stop()
