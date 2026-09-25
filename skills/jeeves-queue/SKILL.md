---
name: jeeves-queue
description: >
  Read, explain, resync, backfill, and repair the Jeeves task queue (supersede
  rules, !list). Dry-run first for any manual edit. Use when queue stale,
  merged PRs still unaccepted, or /jeeves-queue.
---

# jeeves-queue

## Purpose

Operate the deterministic queue: inspect unaccepted/accepted/done, apply
supersede rules, GitHub resync/backfill, and careful repair. Any manual edit
is dry-run first.

Agentic control is an overlay: the token-less path must never depend on this skill.

## TODO: seed FR #N

Populate reducer docs and resync commands after the matching seed FR is numbered.
