---
name: jeeves-announce-debug
description: >
  Debug a GitHub delivery that was not announced: hook deliveries, receiver
  secret-field filter, chair-outbox, Ergo 417 long lines. Use when GIT line
  missing on #bobiverse, ping ok but issues silent, or /jeeves-announce-debug.
---

# jeeves-announce-debug

## Purpose

Trace GitHub → receiver → outbox → Jeeves → `#bobiverse` when a delivery is
missing. Covers hook deliveries, secret-field filter (agentic_irc #206),
chair-outbox drain, and 417 line splits (#205).

Agentic control is an overlay: the token-less path must never depend on this skill.

## FR #24 — length-safe announcements

Module: `src/jeeves/announce.py`

- Vital fields first: event, task (FR/MRB/UAT), `owner/repo#n`, action, URL,
  `fixes:…`, `head:…`; title last (only title truncated with `...`).
- Budget by UTF-8 **bytes** vs IRC 512 (`:nick!user@host PRIVMSG #chan :` + CRLF).
- Compact fallback when vitals alone overflow; never a parser-required continuation.
- Pre-send `validate_before_send` / `prepare_send`; failures log ERROR; queue claim
  still comes from webhook (`process_git_webhook` + `queue_events/`).
- Simulated 417: `simulate_417` / `handle_simulated_417`.
- Tests: `pytest tests/test_announce_length_fr24.py` (+ G1 suite).

## Checklist (delivery missing)

1. GitHub hook delivery → 2xx on `/bob/v1/git`?
2. Secret-field filter only (K14 / agentic_irc #206).
3. `wire_line_bytes(line) <= 512` and `parse_announce(line)` has task+ref.
4. Ergo 417 → log preview; `queue_events/` and claim still present.
5. Never continue a vital field onto a second IRC line.

Do not restart live ionos / Ergo / Jeeves from implementer seats unless assigned.
