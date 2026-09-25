---
name: jeeves-health
description: >
  Check BobJeeves service, IRC reachability (report-only), webhook lastSeen,
  version drift, and throttle backoff. Use when Jeeves down, IRC unreachable,
  BobIrcd Stopped while ergo runs, TipForm offline, or /jeeves-health.
---

# jeeves-health

## Purpose

Diagnose whether Jeeves can work: IRC TCP/TLS reachability, optional service
status (BobJeeves / BobIrcd), digest path. **Report only** for Ergo/BobIrcd
(K7 / FR #8) — never start/stop/edit BobIrcd or `ircd.yaml`. Raise Ergo issues
in **agentic_build #327** for Simon.

Agentic control is an overlay: the token-less path must never depend on this skill.

## Commands

```bash
# from gh-Jeeves checkout
PYTHONPATH=src python -m jeeves.health --json --host irc.ntsa.uk --port 6697
PYTHONPATH=src python -m jeeves health --json   # via package __main__ if wired
```

PowerShell (read-only service query is inside the module on Windows):

```powershell
$env:PYTHONPATH = '<repo>\src'
python -m jeeves.health --json --host irc.ntsa.uk --port 6697
```

Exit code `0` = IRC reachable; `1` = IRC down / degraded for chair path.

## What it checks

| Check | Meaning |
|--------|---------|
| IRC TCP/(TLS) connect | Server listening — **detects IRC down** |
| `BobIrcd` status (optional) | running/stopped/missing — **report only** |
| `BobJeeves` status (optional) | service for chair process |
| `raise_for` | always points Ergo/BobIrcd remediations to agentic_build / Simon |

## Forbidden (CAST IRON)

- `sc start/stop/create/delete BobIrcd`
- `Install-BobIrcd.ps1`, edit `ircd.yaml`
- `nssm install/start/stop BobIrcd`
- Any “heal” that restarts Ergo from this skill

## K7 known drift

**BobIrcd shows Stopped while `ergo.exe` still runs** outside the service.
Health must **note** Stopped + IRC up/down separately and **raise** agentic_build
#327 — not “fix” BobIrcd from gh-Jeeves.

## Tests

```bash
pytest -q tests/cast_iron/test_k7_health_irc_down.py
```

Code: `src/jeeves/health.py`.
