"""FR #160: runtime auto-login / auto-oper for Simon — capability probe.

Ergo (integrated NickServ) has **no** SAIDENTIFY / FORCELOGIN that can log
another connection into account ``simon``. OPER for a human requires an
``opers:`` entry in ``ircd.yaml`` (config change — forbidden by this FR).

What Jeeves *can* do at runtime without editing Ergo:

- FR #52: grant ``+o`` once Simon is already authenticated to account
  ``simon`` from a fleet host (SASL / certfp / PASS account:password).
- Detect an unauthenticated ``simon`` nick from a fleet host and record
  ``no_runtime_path`` (and optionally PM a deterministic hint). Never store
  or invent Simon's password.

Evidence (Ergo upstream NickServ command map): IDENTIFY, GHOST, CERT,
SAREGISTER, SAVERIFY, SADROP, SAGET, SASET, PASSWD (admin reset), SUSPEND —
none force-login another live session. OPER is config-only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from .mode_grants import DEFAULT_FLEET_MACHINES, is_simon_nick, simon_host_ok

log = logging.getLogger("jeeves.simon_auto_auth")

# NickServ SA* / admin verbs that exist in Ergo — none is "log this nick in".
ERGO_NICKSERV_ADMIN_VERBS = frozenset(
    {
        "sadrop",
        "saregister",
        "saverify",
        "saget",
        "saset",
        "list",
        "passwd",
        "cert",
        "suspend",
        "unregister",
        "erase",
        "rename",
    }
)

# Verbs that would be required for true auto-login of another session.
MISSING_FORCE_LOGIN_VERBS = frozenset(
    {
        "saidentify",
        "forcelogin",
        "salogin",
        "su",
        "forceidentify",
    }
)

HINT_PM = (
    "simon: Ergo has no runtime SAIDENTIFY; configure Halloy SASL "
    "(account simon) or PASS simon:<password>, or CERTFP. "
    "OPER requires ircd.yaml (Jeeves will not edit Ergo). "
    "Once SASL'd from a fleet host, Jeeves grants +o (FR #52)."
)


def ergo_has_force_login_sa() -> bool:
    """False: Ergo NickServ does not expose a force-login SA* command."""
    return bool(MISSING_FORCE_LOGIN_VERBS & ERGO_NICKSERV_ADMIN_VERBS)


def ergo_has_runtime_oper_grant() -> bool:
    """False: granting OPER to another nick requires Ergo config, not IRC SA*."""
    return False


def can_runtime_auto_auth_simon() -> bool:
    """True only if both login and oper can be done without Ergo config edits."""
    return ergo_has_force_login_sa() and ergo_has_runtime_oper_grant()


@dataclass
class SimonAutoAuthState:
    # nick.lower() -> reason logged once
    logged: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)
    pms_sent: list[str] = field(default_factory=list)


class SimonAutoAuthController:
    """Observe Simon joins; record no_runtime_path when unauthenticated."""

    def __init__(
        self,
        *,
        send_pm: Callable[[str, str], None] | None = None,
        send_hint: bool = True,
        fleet: frozenset[str] | None = None,
    ):
        self.send_pm = send_pm
        self.send_hint = bool(send_hint)
        self.fleet = fleet or DEFAULT_FLEET_MACHINES
        self.state = SimonAutoAuthState()

    def on_simon_presence(
        self,
        nick: str,
        *,
        account: str | None,
        host: str = "",
        channel: str = "",
    ) -> str | None:
        """Handle a simon-shaped nick sighting.

        Returns action token: ``authenticated``, ``no_runtime_path``,
        ``wrong_host``, ``ignored``, or None.
        """
        n = (nick or "").strip()
        if not is_simon_nick(n):
            return None
        from .focus import owner_account_name

        owner = owner_account_name()
        acct = (account or "").strip().lower()
        authed = bool(acct) and acct not in ("*", "0", "1") and acct == owner

        if authed:
            if not simon_host_ok(host, fleet=self.fleet):
                self._note(n, f"authenticated_non_fleet_host:{host or 'none'}")
                return "wrong_host"
            self._note(n, f"authenticated:{channel or '-'}")
            return "authenticated"

        # Unauthenticated simon nick
        if not simon_host_ok(host, fleet=self.fleet):
            self._note(n, f"unauth_non_fleet:{host or 'none'}")
            return "ignored"

        # Fleet host + reserved nick without account → Halloy reconnect loop case
        key = n.lower()
        if key in self.state.logged:
            return "no_runtime_path"
        self.state.logged.add(key)
        reason = "no_runtime_path"
        self.state.events.append(f"{reason}:{n}:{host}:{channel or '-'}")
        log.warning(
            "simon_auto_auth nick=%s host=%s channel=%s action=%s "
            "ergo_force_login=%s ergo_runtime_oper=%s",
            n,
            host,
            channel or "-",
            reason,
            ergo_has_force_login_sa(),
            ergo_has_runtime_oper_grant(),
        )
        if self.send_hint and self.send_pm:
            try:
                self.send_pm(n, HINT_PM)
                self.state.pms_sent.append(n.lower())
            except Exception:
                log.exception("simon_auto_auth hint PM failed nick=%s", n)
        return reason

    def _note(self, nick: str, event: str) -> None:
        self.state.events.append(event)
        # cap list
        if len(self.state.events) > 80:
            self.state.events = self.state.events[-80:]
