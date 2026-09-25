---
name: jeeves-health
description: >
  Check BobJeeves service, IRC presence in #bobiverse and every #{machine},
  webhook lastSeen, version drift, and throttle backoff. Use when Jeeves down,
  stale lastSeen, TipForm shows Jeeves offline, or /jeeves-health.
---

# jeeves-health

## Purpose

Diagnose whether Jeeves is healthy: service state, nick presence, digest
`lastSeen`, deployed version vs tagged release, and reconnect/throttle backoff.

Agentic control is an overlay: the token-less path must never depend on this skill.

## TODO: seed FR #N

Populate check commands and fail criteria after the matching seed FR is numbered.
