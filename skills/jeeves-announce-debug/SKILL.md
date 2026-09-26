---
name: jeeves-announce-debug
description: >
  Use this when a GitHub event was not announced on #bobiverse: GIT line
  missing, ping OK but issues silent, chair outbox piling up, announce cut off
  or rejected as too long (417), or /jeeves-announce-debug.
---

# jeeves-announce-debug

Seed FR: #20. Related: length-safe lines #24, agentic_irc #205/#206.

## Overlay

**Agentic control is an overlay.** The token-less path (brief section 0 / issue #1)
must **never** depend on this skill or an LLM. Scripts own announce + G1.

## Trace the path

```mermaid
flowchart LR
  GH[GitHub hook delivery] --> RX[receiver /bob/v1/git]
  RX --> F{secret filter + format}
  F -->|ok| Q[queue update]
  F -->|ok| OB[chair-outbox line]
  OB --> J[Jeeves]
  J --> BV["#bobiverse GIT line"]
```

*Caption: find the first hop where the event disappears.*

1. **Hook delivery:** repo Settings → Webhooks → Recent Deliveries. The hook
   posts `issues`, `pull_request`, `push` as JSON to `https://irc.ntsa.uk/bob/v1/git`.
   Redeliver if it failed.
2. **Receiver / secret filter:** scan **only** secret-bearing fields, not the
   whole payload — titles that merely *discuss* `ghp_` must still announce (#206 / FR #15).
3. **Queue:** the item should be in `queue.json` even if IRC failed — the queue
   never depends on IRC text (`jeeves-queue`).
4. **Chair-outbox piling up:** Jeeves not connected or not draining — run
   `jeeves-health`.
5. **Too long (417):** vital fields first (event, type, `owner/repo#n`, action,
   URL), title last and the only part truncated; byte budget, UTF-8 safe; never
   a continuation line a parser needs (#24).

Jeeves announces **only** on `#bobiverse` — a missing GIT line in a shop is expected.

## Commands

CI-safe **dry-run** (sample format + secret-filter demos; never mutates):

```powershell
python -m jeeves.announce_tool --dry-run --json
python -m jeeves announce --dry-run --json
```

Exit codes: `0` ok, `2` skill incomplete.

Live ops checklist (no LLM):

1. GitHub → Webhooks → Recent Deliveries for `/bob/v1/git` (2xx?).
2. Receiver log / `jeeves-health` for connect + `event=announce`.
3. `python -m jeeves.queue_tool --dry-run --json` — was the claim enqueued?
4. Chair-outbox / service log: draining to `#bobiverse`?

## Tests

```text
pytest -q tests/test_skill_announce_debug_fr20.py tests/test_announce_length_fr24.py tests/test_secret_filter_fr15.py
```

Related: `jeeves-health`, `jeeves-queue`.
