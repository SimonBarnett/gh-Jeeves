---
name: jeeves-recycle
description: >
  !recycle is documented and routed by Jeeves; the local bob-{machine} seat
  executes the tray-equivalent worker recycle. Triggers: !recycle, !help recycle,
  fleet worker recycle, FR #197, or /jeeves-recycle.
github: https://github.com/SimonBarnett/gh-Jeeves
---

# !recycle (FR #197)

## Ownership

| Layer | Does |
|-------|------|
| **Jeeves** | Documents in `!help` / `!help recycle`. Authorises. Emits shop wire `RECYCLE machine=… exec=local-bob-seat`. **Never** kills processes, runs git, or reloads skills. |
| **Local `bob-{machine}`** | Validates the route targets this host. Runs tray-equivalent ordered steps via authenticated local control. |

## Authorisation

- Owner services account (same bar as `!sweep`), or authenticated `bob-*` ops.
- Channel op / server-admin alone is **not** enough. Nick alone is **not** enough for the owner path.

## Local ordered steps (bob seat)

Run from the seat that owns the machine (after matching the `RECYCLE` wire):

```powershell
powershell -NoProfile -File tools\Invoke-BobSeatRecycle.ps1 -ExpectedMachine marchhare
# plan only:
powershell -NoProfile -File tools\Invoke-BobSeatRecycle.ps1 -WhatIf
```

1. Stop managed worker processes for this install.
2. Clear **owned/stale** PowerShell, Node, Python for this install only (never unrelated hosts of those names).
3. Fast-forward worker Git checkout only; dirty or non-ff checkout left intact and **reported**.
4. Reload skills (`Reinstall-AgentSkills.ps1` when present).
5. Restart workers via existing tray bootstrap (`Start-BobFleetTray -ForceNew`).

Failed cleanup or git update must be reported; do not silently start from an inconsistent tree. Duplicate `!recycle` is cooldown-blocked on Jeeves.

## Wire

```
RECYCLE machine=marchhare by=simon scope=local exec=local-bob-seat
```

Jeeves PMs the requester a short ack. Shop gets the `RECYCLE` line for the ear.

## Do not

- Recycle Ergo / BobIrcd for a tray paint change.
- Send live `!recycle` from implementer MRB workers (`bob-hostile-mrb`).
- Transmit secrets on IRC.
