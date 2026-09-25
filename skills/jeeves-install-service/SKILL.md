---
name: jeeves-install-service
description: >
  Install, repair, or upgrade BobJeeves (chair+receiver) from config.
  Never touch Ergo or BobIrcd. No BobIrcd service dependency. Use when
  Jeeves missing, wrong IRC cmdline, or /jeeves-install-service.
---

# jeeves-install-service

## Purpose

Idempotent **plan / install / repair** of **BobJeeves**.

### Topology (FR #48) — one documented shape

| Service | Role |
|---------|------|
| **BobJeeves** | **Combined**: `python -m jeeves all` = IRC chair **and** loopback receiver on **127.0.0.1:19781** |

Optional legacy **BobReport** (`Install-BobReport.ps1`) is only for a split deployment; default ionos install is **combined** so IIS can keep proxying to 19781 without a second service.

**No `depend= BobIrcd`.** Ergo may run as plain `ergo.exe`. Jeeves retries IRC with backoff if the network is down.

**CAST IRON:** never create/configure/start/stop **Ergo**, **BobIrcd**, or `ircd.yaml`.

## Config

Copy `config/bobjeeves.example.json` → `config/bobjeeves.json` (or
`%USERPROFILE%\.agentic-irc-jeeves\bobjeeves.json`) and fill:

- `irc_host` / `irc_port` / `tls`
- `nick`, `sasl_user`, `sasl_password_file` (path only — secret never in cmdline)
- `receiver_port` (default **19781**)
- `jeeves_home` / `digest_home` (must differ)

## Commands (CI-safe dry-run first)

```powershell
# Production-shaped plan (prints service_cmdline with --host --port --tls)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-BobJeeves.ps1 -DryRun -Json -Production

powershell -NoProfile -ExecutionPolicy Bypass -File tools\Start-BobJeeves.ps1 -DryRun -Production

# Apply (operators only, elevated) — never from FR workers
# powershell -File tools\Install-BobJeeves.ps1 -Apply -Production -Json
```

Exit codes: `0` ok, `2` validation errors.

## Apply checklist (human / ionos)

1. DryRun: confirm `service_cmdline` has host, port, `--tls`, receiver **19781**, `no_bobircd_dependency`.
2. Elevated `-Apply -Production`.
3. `Get-Service BobJeeves` — starting it must **not** start BobIrcd.
4. Disable task `BobJeeves-chair` after healthy.
5. IIS proxies `https://irc.ntsa.uk/bob/v1/*` → `127.0.0.1:19781`.

## Forbidden

- `depend= BobIrcd`
- `Install-BobIrcd.ps1`, editing `ircd.yaml`
- Embedding SASL password in service AppParameters (file/env only)
- FR worker `-Apply` on production

## Tests

```text
pytest -q tests/test_install_fr48_cmdline.py tests/test_skill_install_service_fr17.py
```

## Related

- FR #48, #17, #39, #46, agentic_build #330
