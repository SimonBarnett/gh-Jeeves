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
#211). Detect stuck accepted rows, stale TipForm tiles, and nick mismatches.

Agentic control is an overlay: the token-less path must never depend on this skill.

## TODO: seed FR #N

Populate state table and repair steps after the matching seed FR is numbered.
