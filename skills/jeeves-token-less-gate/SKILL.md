---
name: jeeves-token-less-gate
description: >
  Run the section 0 token-less end-to-end gate: G1 local test ircd with no-LLM
  guard, and G2 live smoke after deploy. Use when proving the acceptance gate,
  before a release, or /jeeves-token-less-gate.
---

# jeeves-token-less-gate

## Purpose

Execute and record the KEY success metric: GIT announce through to workers
**without tokens**. G1 is CI-required; G2 is manual post-deploy
(`docs/migration-plan.md` §16.3).

Agentic control is an overlay: running this skill helps operators; the gate
itself is script-only and must never require an LLM.

## TODO: seed FR #N

Populate pytest invocation and G2 checklist after the matching seed FR (headline
#1) lands.
