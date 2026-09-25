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

Machine-read lines use `src/gh_jeeves/announce.py`:

- Vital fields first (event, task FR/MRB/UAT, `owner/repo#n`, action, URL,
  `fixes:…`, `head:…`); title last; only the title is truncated (`...`).
- Budget by UTF-8 **bytes** vs IRC 512 including `:nick!user@host PRIVMSG #chan :`
  and CRLF (`text_budget` / `wire_line_bytes`). Never cut mid-codepoint.
- Compact fallback when vitals alone overflow; never a parser-required
  continuation line.
- Pre-send: `validate_before_send` / `prepare_send` round-trip with `parse_announce`.
  Failures log ERROR; queue still written from webhook vitals (`MemoryQueue` /
  `QueueEvent`) — queue never depends on IRC text.
- Simulated 417: `simulate_417` logs preview; queue item remains.
- Tests (token-less, gate surface): `pytest tests/test_announce_length_fr24.py`

## Checklist (delivery missing)

1. GitHub hook delivery → 2xx on `/bob/v1/git`?
2. Secret-field filter (K14 / agentic_irc #206) — scan emitted fields only.
3. `prepare_send` body under budget (`wire_line_bytes(line) <= 512`).
4. Ergo 417 → log `ERROR 417`; queue event still present.
5. Never continue a vital field onto a second IRC line.

Do **not** restart live ionos / Ergo / Jeeves from implementer seats unless assigned.
