---
name: jeeves-release
description: >
  Tag a gh-Jeeves release, deploy from the tag on ionos, and roll back to the
  previous release or legacy BobJeeves-chair task. Use when shipping Jeeves,
  version drift, or /jeeves-release.
---

# jeeves-release

## Purpose

Release hygiene: tag, deploy from release (not hotpatch worktree), record G2
smoke in release notes, roll back safely. Deployed version must be reported to
the webhook for drift checks.

Agentic control is an overlay: the token-less path must never depend on this skill.

## TODO: seed FR #N

Populate tag/deploy/rollback commands after the matching seed FR is numbered.
