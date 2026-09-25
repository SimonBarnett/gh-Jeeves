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
- **Auth (FR #72):**
  - **Server-password (ionos live):** leave `sasl_user` empty; set `password_file`
    to the Ergo server password path. Installer sets `AGENTIC_IRC_PASSWORD_FILE`
    on the service (LocalSystem cannot use your user profile fallback alone —
    expand to an absolute path operators can read as SYSTEM, or place the file
    where SYSTEM can read it).
  - **SASL:** set `sasl_user` + `sasl_password_file` instead.
- **Receiver secret (FR #72):** `receiver_secret_file` → nssm
  `BOB_CALLBACK_SECRET_FILE` for `X-Bob-Secret` (e.g. `…\.grok\bob\report.secret`).
  Without this, LocalSystem will not see a user-home secret.
- **Resync (FR #72):** `disable_resync: true` adds `--no-resync` and
  `JEEVES_RESYNC_DISABLE=1` (hosts without a GitHub token).
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
2. DryRun JSON (FR #72): `receiver_secret_file`, `password_file` or SASL files, `auth_mode`,
   `disable_resync` as needed — **no hand edits** to nssm env after Apply.
3. Elevated `-Apply -Production`.
4. `Get-Service BobJeeves` — starting it must **not** start BobIrcd.
5. Disable task `BobJeeves-chair` after healthy.
6. IIS proxies `https://irc.ntsa.uk/bob/v1/*` → `127.0.0.1:19781`.
7. **Outbox pos (FR #71 / cutover):** digest home may still have agentic_irc `chair-outbox.txt.pos`. gh-Jeeves writes `chair-outbox.pos`. First start **migrates** the legacy file; if neither exists and the outbox is non-empty, start is **EOF** (no replay flood to `#bobiverse`). Empty / whitespace / garbage / negative pos files are treated as missing (EOF park) — never as `0`. Do not pass `--replay-outbox` on production cutover unless operators intentionally want a full re-announce. See `docs/receiver-bobcallback-cutover.md`.

## Forbidden

- `depend= BobIrcd`
- `Install-BobIrcd.ps1`, editing `ircd.yaml`
- Embedding SASL password in service AppParameters (file/env only)
- FR worker `-Apply` on production

## Tests

```text
pytest -q tests/test_install_fr48_cmdline.py tests/test_skill_install_service_fr17.py tests/test_outbox_pos_fr71.py
python -m pytest tests/ -q
```

## Related

- FR #48, #17, #39, #46, #71, #72, agentic_build #330
