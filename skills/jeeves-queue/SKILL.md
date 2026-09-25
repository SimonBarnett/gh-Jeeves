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

**Ignore list (FR #75):** `!ignore {repo}` / `!unignore {repo}` / `!ignored`.
Persists as `ignored.json` next to `queue.json`. Ignored repos are dropped from
announce, queue, `!list`, and ear offers. Purge on ignore; unignore is new
events only. Dry-run before hand-editing `ignored.json`.

**Focus (FR #68):** `!focus [n|high|medium|low] {repo}` / `!unfocus {repo}|all`
(simon account). `focus.json` beside queue. One sort for `!list` and ear
`top_unaccepted` (!bored). Ignored still wins over focus.

Agentic control is an overlay: the token-less path must never depend on this skill.
