"""FR #170: additive JEEVES_FOCUS_MUTATORS / _ACCOUNTS for !focus/!unfocus."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jeeves.focus import (
    focus_mutator_accounts,
    focus_mutator_nicks,
    focus_mutator_via,
    load_focus,
    may_mutate_focus,
)
from jeeves.local_ircd import IrcClient, LocalIrcd
from jeeves.queue import Claim, apply_queue_event
from jeeves.roles import JeevesChair


def test_empty_env_keeps_owner_only(monkeypatch):
    monkeypatch.delenv("JEEVES_FOCUS_MUTATORS", raising=False)
    monkeypatch.delenv("JEEVES_FOCUS_MUTATOR_ACCOUNTS", raising=False)
    assert focus_mutator_nicks() == set()
    assert focus_mutator_accounts() == set()
    assert may_mutate_focus("simon", account="simon", mode_grants_live=True)
    assert not may_mutate_focus("bob-ionos", account=None, mode_grants_live=True)
    assert not may_mutate_focus("bob-x", account=None, mode_grants_live=False)


def test_allowlisted_nick_exact_only(monkeypatch):
    monkeypatch.setenv("JEEVES_FOCUS_MUTATORS", "bob-ionos, bob-flamingo")
    assert focus_mutator_nicks() == {"bob-ionos", "bob-flamingo"}
    assert may_mutate_focus("bob-ionos", account=None, mode_grants_live=True)
    assert may_mutate_focus("bob-flamingo", account=None, mode_grants_live=False)
    # No wildcards: bob-x and bob-ionos-extra denied
    assert not may_mutate_focus("bob-x", account=None, mode_grants_live=True)
    assert not may_mutate_focus("bob-ionos-extra", account=None, mode_grants_live=True)
    assert focus_mutator_via("bob-ionos", account=None, mode_grants_live=True) == "allowlist"


def test_owner_still_allowed_with_allowlist(monkeypatch):
    monkeypatch.setenv("JEEVES_FOCUS_MUTATORS", "bob-ionos")
    assert may_mutate_focus("simon", account="simon", mode_grants_live=True)
    assert may_mutate_focus("simon-laptop", account="simon", mode_grants_live=True)
    assert focus_mutator_via("simon", account="simon", mode_grants_live=True) == "owner"
    # Owner still needs account when live
    assert not may_mutate_focus("simon-laptop", account=None, mode_grants_live=True)


def test_allowlist_account_additive(monkeypatch):
    monkeypatch.delenv("JEEVES_FOCUS_MUTATORS", raising=False)
    monkeypatch.setenv("JEEVES_FOCUS_MUTATOR_ACCOUNTS", "bobfleet")
    assert may_mutate_focus("anything", account="bobfleet", mode_grants_live=True)
    assert not may_mutate_focus("anything", account="other", mode_grants_live=True)
    assert focus_mutator_via("x", account="bobfleet", mode_grants_live=True) == "allowlist"


def test_chair_bob_ionos_focus_and_strict(
    tmp_path: Path, monkeypatch, caplog
):
    monkeypatch.setenv("JEEVES_FOCUS_MUTATORS", "bob-ionos")
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(
        home, Claim("SimonBarnett/agentic_build", "FR", "#369", line="x")
    )
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair(
            "127.0.0.1", port, home, "http://127.0.0.1:9", client=client
        )
        assert chair.mode_grants is not None
        # No account for bob-ionos → nick trust (live=True, account=none)
        assert chair._account_for_nick("bob-ionos") in (None, "")
        assert chair._focus_mutator_ok("bob-ionos") is True
        assert chair._focus_mutator_via("bob-ionos") == "allowlist"

        with caplog.at_level(logging.INFO, logger="jeeves.chair"):
            chair._handle_focus_cmds(
                "bob-ionos", "!focus 9 SimonBarnett/agentic_build#369"
            )
        assert any(h.startswith("focus:bob-ionos:") for h in chair.handled)
        foc = load_focus(home)
        items = foc.get("items") or {}
        assert "SimonBarnett/agentic_build#369" in items
        assert int(items["SimonBarnett/agentic_build#369"]["rank"]) == 9
        assert any(
            "via=allowlist" in r.getMessage() and "cmd=focus" in r.getMessage()
            for r in caplog.records
        ), caplog.text
        texts = [t for _, t in chair.pm_egress]
        assert any(
            "focus: item SimonBarnett/agentic_build#369" in t and "rank=9" in t
            for t in texts
        ), texts

        chair.pm_egress.clear()
        chair.handled.clear()
        with caplog.at_level(logging.INFO, logger="jeeves.chair"):
            chair._handle_focus_cmds("bob-ionos", "!focus strict on")
        texts2 = [t for _, t in chair.pm_egress]
        assert any("focus strict: on" in t for t in texts2), texts2
        assert any(
            "via=allowlist" in r.getMessage() for r in caplog.records
        ), caplog.text
    finally:
        chair.stop()
        client.close()
        ircd.stop()


def test_chair_non_listed_bob_denied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JEEVES_FOCUS_MUTATORS", "bob-ionos")
    home = tmp_path / "d"
    home.mkdir()
    apply_queue_event(home, Claim("o/r", "FR", "#1", line="x"))
    ircd = LocalIrcd()
    port = ircd.start()
    try:
        client = IrcClient("127.0.0.1", port, "Jeeves")
        chair = JeevesChair(
            "127.0.0.1", port, home, "http://127.0.0.1:9", client=client
        )
        assert chair._focus_mutator_ok("bob-x") is False
        chair._handle_focus_cmds("bob-x", "!focus o/r")
        assert any("focus_denied:bob-x" in h for h in chair.handled), chair.handled
    finally:
        chair.stop()
        client.close()
        ircd.stop()
