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

## TODO: seed FR #N

Populate delivery checklist after the matching seed FR is numbered.
