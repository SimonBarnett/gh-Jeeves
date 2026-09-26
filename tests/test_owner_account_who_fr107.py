"""FR #107: simon-* account lookup, WHO fallback, denial log fields."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from jeeves.focus import may_mutate_focus, owner_account_name
from jeeves.ignore import may_mutate_ignore
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.mode_grants import ModeGrantController
from jeeves.queue import Claim, apply_queue_event
from jeeves.roles import JeevesChair


def test_owner_account_from_env(monkeypatch):
    monkeypatch.setenv("JEEVES_OWNER_ACCOUNT", "Simon")
    assert owner_account_name() == "simon"
    monkeypatch.delenv("JEEVES_OWNER_ACCOUNT", raising=False)
    assert owner_account_name() == "simon"


def test_simon_dash_sender_uses_own_account_not_simon_key(tmp_path: Path):
    """simon-laptop must check accounts['simon-laptop'], not accounts['simon']."""
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(home, Claim("o/r", "FR", "#1", line="x"))
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair("127.0.0.1", port, home, "http://127.0.0.1:9", client=client)
        assert chair.mode_grants is not None
        # Wrong: only bare simon has account — old bug would use that for simon-laptop
        chair.mode_grants.set_account("simon", "simon")
        # Correct path: sender's nick has the owner account
        chair.mode_grants.set_account("simon-laptop", "simon")
        assert chair._focus_mutator_ok("simon-laptop") is True
        # If only a different nick holds the account, simon-laptop must fail
        chair.mode_grants.state.accounts.pop("simon-laptop", None)
        assert chair._focus_mutator_ok("simon-laptop") is False
    finally:
        chair.stop()
        client.close()
        ircd.stop()


def test_whox_354_sets_account_for_preexisting_nick(tmp_path: Path):
    """User present before Jeeves joined: WHOX 354 fills account."""
    ctrl = ModeGrantController(client=_RawCatch(), jeeves_nick="Jeeves")
    # Simulate WHOX reply for nick already in #marchhare
    ctrl.handle_raw(":irc 354 Jeeves simon-laptop simon host.example Hx")
    assert ctrl.state.accounts.get("simon-laptop") == "simon"


def test_whois_330_sets_account(tmp_path: Path):
    ctrl = ModeGrantController(client=_RawCatch(), jeeves_nick="Jeeves")
    ctrl.handle_raw(":irc 330 Jeeves simon-laptop simon :is logged in as")
    assert ctrl.state.accounts.get("simon-laptop") == "simon"


def test_who_sent_after_self_join(tmp_path: Path):
    catch = _RawCatch()
    ctrl = ModeGrantController(client=catch, jeeves_nick="Jeeves")
    ctrl.handle_raw(":Jeeves!u@h JOIN #marchhare")
    assert any(x.upper().startswith("WHO #MARCHHARE") for x in catch.raw)


def test_focus_denied_logs_account_and_live(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(home, Claim("o/r", "FR", "#1", line="x"))
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair("127.0.0.1", port, home, "http://127.0.0.1:9", client=client)
        # mode_grants live, no account for simon-laptop → deny
        assert chair.mode_grants is not None
        with caplog.at_level(logging.INFO, logger="jeeves.chair"):
            chair._handle_focus_cmds("simon-laptop", "!focus o/r")
        assert any("focus_denied" in h for h in chair.handled)
        msgs = [
            r.getMessage()
            for r in caplog.records
            if "focus" in r.getMessage() and "denied" in r.getMessage()
        ]
        assert msgs, caplog.text
        assert any("account=" in m and "live=" in m for m in msgs), msgs
    finally:
        client.close()
        ircd.stop()


def test_may_mutate_uses_owner_account_env(monkeypatch):
    monkeypatch.setenv("JEEVES_OWNER_ACCOUNT", "ownerbob")
    assert may_mutate_focus("ownerbob", account="ownerbob", mode_grants_live=True)
    assert may_mutate_focus("ownerbob-laptop", account="ownerbob", mode_grants_live=True)
    assert not may_mutate_focus("simon", account="ownerbob", mode_grants_live=True)
    assert may_mutate_ignore("ownerbob-x", account="ownerbob")
    assert not may_mutate_ignore("simon-x", account="simon")


class _RawCatch:
    def __init__(self) -> None:
        self.raw: list[str] = []

    def send_raw(self, line: str) -> None:
        self.raw.append(line)
