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
(`docs/migration-plan.md` section 16.3).

Agentic control is an overlay: running this skill helps operators; the gate
itself is script-only and must never require an LLM.

## G1 (CI, every PR)

```bash
pip install -e ".[dev]"
pytest -q tests/
```

Includes:

- `tests/g1_token_less_e2e` — local test ircd + stub receiver + scripted worker
- `tests/test_announce_length_fr24.py` — FR #24 length-safe announce (gate-blocking)

Pass criteria:

- Chain: GitHub event to GIT on #bobiverse to queue FR to !bored to ear OFFER to ACK to DONE
- Jeeves never handles !bored / never offers
- Worker nick {machine}-{pid} accepted
- Announce lines vital-first, byte-budgeted (FR #24); queue from webhook not IRC
- No-LLM guard: AI env scrubbed; non-loopback TCP blocked
- Never live IRC / ionos / Ergo

## G2 (manual after deploy)

Follow `docs/migration-plan.md` section 16.3 and `docs/g2-live-smoke-checklist.md`
(including the **TLS IRC FR #46** line: native `python -m jeeves --tls`, not
`agentic_irc` `irc_agent --chair`). Record in release notes. Do not stamp UAT
from this skill.

## G1 TLS path (FR #46)

```text
pytest -q tests/g1_token_less_e2e/test_g1_tls_path.py
```