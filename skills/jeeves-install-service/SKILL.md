---
name: jeeves-install-service
description: >
  Install, repair, or upgrade BobJeeves and BobReport Windows services
  idempotently. Never touch Ergo or BobIrcd. Use when Jeeves/receiver missing,
  Disabled, ad-hoc BobReport-ionos task still owning the receiver, or
  /jeeves-install-service.
---

# jeeves-install-service

## Purpose

Idempotent **plan / install / repair** of:

| Service | Role | Script |
|---------|------|--------|
| **BobJeeves** | IRC chair | `tools/Install-BobJeeves.ps1` (FR #17) |
| **BobReport** | GIT/digest HTTP receiver | `tools/Install-BobReport.ps1` (FR #9 / K8) |

**K8:** the receiver is **owned in this repo**. Do not use the ad-hoc Administrator
profile launcher `Start-BobReport-ionos.ps1` / task `BobReport-ionos` as source of
truth — replace it with service **BobReport** after a dry-run plan.

**CAST IRON:** never create/configure/start/stop **Ergo**, **BobIrcd**, or
`ircd.yaml`.

**Agentic control is an overlay.** Token-less G1 never depends on this skill.

## Homes

| Path | Role |
|------|------|
| `%USERPROFILE%\.agentic-irc-jeeves` | Chair home |
| `%USERPROFILE%\.agentic-irc-bobiverse` | Digest / queue / receiver (`BOB_DIGEST_HOME`) |

## Commands (CI-safe dry-run first)

```powershell
# Chair
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -DryRun -Json
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Start-BobJeeves.ps1 -DryRun

# Receiver (K8) — default bind 127.0.0.1:19781
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobReport.ps1 -DryRun -Json
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Start-BobReport.ps1 -DryRun

# Entry point used by the service
# python -m jeeves receiver --digest-home $env:USERPROFILE\.agentic-irc-bobiverse --receiver-bind 127.0.0.1 --receiver-port 19781

# Chair on Ergo TLS (FR #46 native client — no agentic_irc irc_agent)
# python -m jeeves chair --tls --host irc.ntsa.uk --port 6697
# Optional: JEEVES_TLS_CAFILE, JEEVES_TLS_PIN_SHA256, AGENTIC_IRC_SASL_USER/PASSWORD
```

Exit codes: `0` ok, `2` validation errors.

## Apply checklist (human / ionos operator only)

1. `-DryRun -Json` for **both** installers; confirm `never_touch_ircd`.
2. Elevated: `Install-BobReport.ps1 -Apply` then `Install-BobJeeves.ps1 -Apply` as needed.
3. `Get-Service BobReport`, `Get-Service BobJeeves`.
4. Disable tasks `BobReport-ionos` and `BobJeeves-chair` only after services healthy.
5. IIS still proxies `https://irc.ntsa.uk/bob/v1/*` to loopback (operator).

## Forbidden

- Profile scripts under `Administrator` as the durable launcher
- `Install-BobIrcd.ps1`, editing `ircd.yaml`, `sc.exe * BobIrcd`
- FR worker `-Apply` on production

## Tests

```text
pytest -q tests/test_skill_install_service_fr17.py tests/test_install_receiver_fr9.py
```

## Related

- FR #9 K8, FR #17, FR #39 `python -m jeeves`, agentic_irc #206
