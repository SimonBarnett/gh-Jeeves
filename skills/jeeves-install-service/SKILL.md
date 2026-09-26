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
- `password_file` with empty `sasl_user` → Ergo **server-password** auth (`AGENTIC_IRC_PASSWORD_FILE`)
- `receiver_secret_file` → nssm `BOB_CALLBACK_SECRET_FILE` (LocalSystem X-Bob-Secret; FR #72)
- `disable_resync` / `-ResyncDisable` / `-DisableResync` → `--no-resync` + `JEEVES_RESYNC_DISABLE=1`
- `receiver_port` (default **19781**)
- `jeeves_home` / `digest_home` (must differ)
- `JEEVES_OWNER_ACCOUNT` (default `simon`) — Ergo services account that may
  `!focus` / `!unfocus` / `!ignore` / `!sweep` (nick `simon` or `simon-*`)

### Owner account on Ergo (FR #107)

`!focus` / `!sweep` / `!ignore` require a **registered** services account matching
`JEEVES_OWNER_ACCOUNT` (default `simon`). Nick alone is not enough when mode
grants are live. Jeeves learns accounts via extended-join, account-notify, and
**WHO/WHOX** after JOIN (for nicks already in the channel).

If `accounts.registration.enabled` is false on Ergo (ionos), an oper must create
the account, for example:

```text
/NS SAREGISTER simon <password>
```

Then identify as that account from the fleet host. Without this, focus/ignore
denials log `account=none live=True`.

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

1. DryRun: confirm `service_cmdline` has host, port, `--tls`, receiver **19781**, `no_bobircd_dependency`, **`start_type=auto`**, **`disable_task=true`** (BobJeeves-chair), **`recovery_restart=true`**.
2. Elevated `-Apply -Production`.
3. `Get-Service BobJeeves` — **StartType Automatic** (never Disabled); starting it must **not** start BobIrcd.
4. Apply **Disable-ScheduledTask BobJeeves-chair** itself (FR #7 / K6) — do not leave Jeeves on the legacy task.
5. Failure recovery: `sc failure` restart/60s ×3 (Apply sets this).
6. IIS proxies `https://irc.ntsa.uk/bob/v1/*` → `127.0.0.1:19781`.
7. **Outbox pos (FR #71 / cutover):** digest home may still have agentic_irc `chair-outbox.txt.pos`. gh-Jeeves writes `chair-outbox.pos`. First start **migrates** the legacy file; if neither exists and the outbox is non-empty, start is **EOF** (no replay flood to `#bobiverse`). Empty / whitespace / garbage / negative pos files are treated as missing (EOF park) — never as `0`. Do not pass `--replay-outbox` on production cutover unless operators intentionally want a full re-announce. See `docs/receiver-bobcallback-cutover.md`.

## Forbidden

- `depend= BobIrcd`
- `Install-BobIrcd.ps1`, editing `ircd.yaml`
- Embedding SASL password in service AppParameters (file/env only)
- FR worker `-Apply` on production

## Tests

```text
pytest -q tests/test_install_fr48_cmdline.py tests/test_skill_install_service_fr17.py tests/test_outbox_pos_fr71.py tests/test_k6_service_replaces_task_fr7.py
```

## Related

- FR #7 (K6 service replaces task), #48, #17, #39, #46, #71, agentic_build #330
