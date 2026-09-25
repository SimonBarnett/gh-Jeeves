---
name: jeeves-install-service
description: >
  Install, repair, or upgrade the BobJeeves Windows service idempotently.
  Never touch Ergo or BobIrcd. Use when Jeeves service missing, Disabled,
  scheduled task BobJeeves-chair still owning the chair, or /jeeves-install-service.
---

# jeeves-install-service

## Purpose

Idempotent **plan / install / repair / upgrade** of Windows service **`BobJeeves`**
from a gh-Jeeves tree (agentic_build #330, FR #17). Replaces the scheduled task
`BobJeeves-chair` once the service is healthy.

**CAST IRON:** never create, configure, start, stop, or edit **Ergo**, **BobIrcd**,
or `ircd.yaml`. Dependency on BobIrcd is declare-only (`depend=`).

**Agentic control is an overlay.** The token-less path (brief §0 / FR #1) must
**never** depend on this skill or an LLM. G1 stays script-only.

## Homes (must differ)

| Path | Role |
|------|------|
| `%USERPROFILE%\.agentic-irc-jeeves` | Chair IRC home (`JeevesHome`) |
| `%USERPROFILE%\.agentic-irc-bobiverse` | Digest / queue / chair-outbox (`BOB_DIGEST_HOME`) |

If they are the same path, the installer fails the plan.

## Commands (CI-safe dry-run first)

From the gh-Jeeves repo root:

```powershell
# Plan only (default) — no SCM mutation; safe on any box / in tests
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -DryRun -Json

# Start-helper dry-run
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Start-BobJeeves.ps1 -DryRun

# Apply (operators only, elevated). NEVER run from FR workers or G1.
# powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -Apply
```

Exit codes: `0` plan/apply ok, `2` validation errors.

## Apply checklist (human / ionos operator)

1. Run `-DryRun -Json` and confirm `never_touch_ircd`, `homes_distinct`, steps.
2. Confirm BobIrcd already exists (do **not** install it from this skill).
3. Elevated PowerShell: `-Apply`.
4. `Get-Service BobJeeves` → Running or start it.
5. Disable task `BobJeeves-chair` only after the service is healthy.
6. Verify with skill `jeeves-health` (separate FR).

## Forbidden

- `Install-BobIrcd.ps1`, editing `ircd.yaml`, `sc.exe * BobIrcd`
- Installing NSSM/Ergo from this skill (read existing `nssm.exe` path only)
- Putting secrets in the skill, plan JSON committed to git, or chat

## Tests

```text
pytest -q tests/test_skill_install_service_fr17.py
```

## Related

- agentic_build #330, agentic_irc chair install scripts
- Skills: `jeeves-health`, `jeeves-release`, `jeeves-token-less-gate`
