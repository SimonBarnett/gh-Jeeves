---
name: jeeves-worker-state
description: >
  Interpret ACK/DONE, busy/idle on the digest webhook, stale START tiles, and
  stuck accepted items. Use when accepted stays empty after ACK, worker looks
  idle while working, or /jeeves-worker-state.
---

# jeeves-worker-state

## Purpose

Explain and repair worker busy/idle derived from shop ACK/DONE (agentic_irc
#211 / gh-Jeeves FR #4 K3). Detect stuck accepted rows, stale TipForm tiles,
and nick mismatches.

Agentic control is an overlay: the token-less path must never depend on this skill.

## K3 — accept on ACK (gate-blocking)

| Signal | Expected |
|--------|----------|
| Shop `ACK FR\|MRB\|UAT owner/repo#n` | `accept_job` → row moves unaccepted → accepted |
| Webhook | `op=queue_accept` + `op=worker_state` state=busy |
| `queue.json` / `GET /bob/v1/report` | `accepted.length >= 1`, worker busy |
| `DONE …` | accepted → done; worker idle |
| `NACK\|GIVEUP …` | accepted → unaccepted; worker idle |

Code: `src/jeeves/queue.py` (`accept_job`, `complete_job`, `nack_job`),
`src/jeeves/roles.py` (`JeevesChair._handle_shop`), `src/jeeves/wire.py` (`parse_ack`).

Tests: `pytest tests/cast_iron/test_k3_accept_on_ack.py`

## Repair checklist (live ionos — ops only)

1. Confirm chair hears shop PRIVMSG (joined `#{machine}`).
2. Confirm ACK grammar matches `parse_ack`.
3. Confirm digest home is the same path the chair writes.
4. Do **not** restart Ergo/BobIrcd from implementer seats unless assigned.
