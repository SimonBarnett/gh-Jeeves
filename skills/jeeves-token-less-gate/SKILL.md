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

## G1 (CI, every PR)

```bash
pip install -e ".[dev]"
pytest -q tests/g1_token_less_e2e
```

Pass criteria:

- Local test ircd + stub `/bob/v1/git` + `/bob/v1/report` + scripted worker
- Chain: GitHub event → `GIT …` on `#bobiverse` → queue FR → `!bored` → ear
  `OFFER` → `ACK` → accepted+busy → `DONE` → done+idle
- Jeeves never handles `!bored` / never offers
- Worker nick `{machine}-{pid}` accepted
- No-LLM guard: AI env scrubbed; non-loopback TCP blocked
- Never live IRC / ionos / Ergo

## G2 (manual after deploy)

Follow `docs/migration-plan.md` section 16.3; record in release notes. Do not
stamp UAT from this skill.
