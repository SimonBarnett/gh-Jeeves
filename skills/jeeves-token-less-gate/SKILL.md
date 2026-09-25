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

## G1 surface (local, no live IRC)

```powershell
cd <temp clone of gh-Jeeves>
python -m pytest tests/test_announce_length_fr24.py -q
```

FR #24 (gate-blocking length-safe announce) is part of the #1 token-less gate
path: every machine-read line must round-trip parse with vitals intact.
