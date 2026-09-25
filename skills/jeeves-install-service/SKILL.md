---
name: jeeves-install-service
description: >
  Install, repair, or upgrade the BobJeeves Windows service idempotently.
  Never touch Ergo or BobIrcd. Use when Jeeves service missing, Disabled,
  scheduled task BobJeeves-chair still owning the chair, or /jeeves-install-service.
---

# jeeves-install-service

## Purpose

Idempotent install/repair/upgrade of Windows service `BobJeeves` from a
gh-Jeeves release (agentic_build #330). Replaces or disables task
`BobJeeves-chair`. Never edits Ergo or `BobIrcd`.

Agentic control is an overlay: the token-less path must never depend on this skill.

## TODO: seed FR #N

Populate commands, DPAPI/service-account notes, and acceptance tests after the
matching seed FR is numbered on this repo.
