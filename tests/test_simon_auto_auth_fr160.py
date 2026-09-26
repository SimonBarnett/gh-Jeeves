"""FR #160: auto-oper/login Simon — Ergo has no runtime SAIDENTIFY; document path."""
from __future__ import annotations

from jeeves.mode_grants import ModeGrantController, desired_mode, Presence
from jeeves.simon_auto_auth import (
    ERGO_NICKSERV_ADMIN_VERBS,
    HINT_PM,
    MISSING_FORCE_LOGIN_VERBS,
    SimonAutoAuthController,
    can_runtime_auto_auth_simon,
    ergo_has_force_login_sa,
    ergo_has_runtime_oper_grant,
)


class _FakeClient:
    def __init__(self) -> None:
        self.raw: list[str] = []

    def send_raw(self, line: str) -> None:
        self.raw.append(line)


def test_ergo_has_no_force_login_sa_command():
    assert ergo_has_force_login_sa() is False
    assert not (MISSING_FORCE_LOGIN_VERBS & ERGO_NICKSERV_ADMIN_VERBS)
    # Known admin verbs exist; none is saidentify
    assert "saregister" in ERGO_NICKSERV_ADMIN_VERBS
    assert "saidentify" not in ERGO_NICKSERV_ADMIN_VERBS


def test_ergo_has_no_runtime_oper_grant():
    assert ergo_has_runtime_oper_grant() is False
    assert can_runtime_auto_auth_simon() is False


def test_authenticated_simon_from_fleet_still_gets_plus_o():
    """FR #52 path remains: SASL'd simon + fleet host → +o (not this FR's SA*)."""
    p = Presence(
        nick="simon",
        account="simon",
        host="user/flamingo.irc.ntsa.uk",
        channel="#bobiverse",
        modes="",
    )
    assert desired_mode(p) == "o"


def test_unauthenticated_simon_fleet_host_records_no_runtime_path():
    pms: list[tuple[str, str]] = []

    def send_pm(nick: str, msg: str) -> None:
        pms.append((nick, msg))

    ctrl = SimonAutoAuthController(send_pm=send_pm, send_hint=True)
    action = ctrl.on_simon_presence(
        "simon",
        account=None,
        host="cloak/marchhare.irc.ntsa.uk",
        channel="#marchhare",
    )
    assert action == "no_runtime_path"
    assert any(e.startswith("no_runtime_path:simon:") for e in ctrl.state.events)
    assert pms and pms[0][0] == "simon"
    assert "SAIDENTIFY" in pms[0][1]
    assert "ircd.yaml" in pms[0][1]
    assert HINT_PM in pms[0][1] or "SASL" in pms[0][1]
    # once only
    assert (
        ctrl.on_simon_presence(
            "simon", account="*", host="cloak/marchhare.irc.ntsa.uk", channel="#marchhare"
        )
        == "no_runtime_path"
    )
    assert len(pms) == 1


def test_unauthenticated_simon_non_fleet_ignored():
    ctrl = SimonAutoAuthController(send_pm=lambda *_: None, send_hint=True)
    assert (
        ctrl.on_simon_presence("simon", account=None, host="random.isp.example", channel="#x")
        == "ignored"
    )
    assert not ctrl.state.pms_sent


def test_authenticated_simon_wrong_host():
    ctrl = SimonAutoAuthController(send_hint=False)
    assert (
        ctrl.on_simon_presence(
            "simon", account="simon", host="coffee-shop.wifi", channel="#bobiverse"
        )
        == "wrong_host"
    )


def test_mode_grant_controller_wires_simon_auto():
    client = _FakeClient()
    mg = ModeGrantController(client, jeeves_nick="Jeeves", rate_s=0.0)
    pms: list[str] = []
    mg.simon_auto = SimonAutoAuthController(
        send_pm=lambda n, m: pms.append(m), send_hint=True
    )
    # JOIN-shaped: unauth simon from flamingo cloak
    mg.set_host("simon", "ip/flamingo.example")
    out = mg.maybe_grant("simon", "#bobiverse")
    assert out is None
    assert not any(r.startswith("MODE ") for r in client.raw)
    assert pms and "SAIDENTIFY" in pms[0]
    assert any("no_runtime_path" in e for e in mg.simon_auto.state.events)


def test_source_documents_no_ergo_config_edit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    src = (root / "src" / "jeeves" / "simon_auto_auth.py").read_text(encoding="utf-8")
    doc = (root / "docs" / "simon-auto-oper-runtime-fr160.md").read_text(encoding="utf-8")
    assert "ircd.yaml" in src or "ircd.yaml" in doc
    assert "SAIDENTIFY" in doc
    assert "Do not change Ergo" in doc or "must not edit Ergo" in doc.lower() or "forbidden" in doc.lower()
