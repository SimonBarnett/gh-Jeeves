---
name: jeeves-queue
description: >
  Read, explain, resync, backfill, and repair the Jeeves task queue (supersede
  rules, !list). Dry-run first for any manual edit. Use when queue stale,
  merged PRs still unaccepted, or /jeeves-queue.
---

# jeeves-queue

Seed FR: #19 (also #25 resync, #27 !help).

## Overlay

**Agentic control is an overlay.** The token-less path (brief section 0 / issue #1)
must **never** depend on this skill or an LLM. Scripts own supersede and G1.
Any **manual** edit of `queue.json` / `ignored.json` / `focus.json` is
**dry-run first**.

## Purpose

Operate the deterministic queue: inspect unaccepted/accepted/done, apply
supersede rules, GitHub resync/backfill, and careful repair.

| File (beside digest home) | Role |
|---------------------------|------|
| `queue.json` | Crash mirror: unaccepted / accepted / done / workers / `mrb_fail_hold` |
| `ignored.json` | FR #75 repo suppress list |
| `focus.json` | FR #68 / #113 focus priorities |

**Ignore list (FR #75):** `!ignore {repo}` / `!unignore {repo}` / `!ignored`.
Ignored repos are dropped from announce, queue, `!list`, and assign-on-`!bored`.

**Focus (FR #68 / #113):** `!focus` / `!unfocus` (simon). Same sort for `!list`
and Jeeves assign-on-`!bored` (FR #106). Item `owner/repo#N` beats repo focus.

## Supersede

| Event | Queue change |
|-------|----------------|
| issue opened / reopened | enqueue **FR** |
| PR opened (`Closes/Fixes/Resolves #n`) | drop FR #n; enqueue **MRB** |
| PR merged (MRB PASS) | drop MRB; enqueue **UAT** |
| PR closed unmerged | drop MRB; restore **FR** if issue still open |
| `DONE MRB … FAIL` | restore **FR**; `mrb_fail_hold` (K15 / FR #16) |
| issue closed | drop FR/UAT unless `mrb_fail_hold` |

Replay is idempotent. Live source of truth is the GitHub webhook; `queue.json`
is the crash mirror. Resync backfills open items and drops closed/superseded.

## Commands

CI-safe **dry-run** (read-only; never writes `queue.json`):

```powershell
python -m jeeves.queue_tool --dry-run --json
python -m jeeves queue --dry-run --json

# optional explicit home
python -m jeeves.queue_tool --dry-run --json --digest-home $env:BOB_DIGEST_HOME
```

Live IRC (ops / simon as documented):

| Line | Effect |
|------|--------|
| `!list` / `!list all` / `!list {repo}` | Queue by **PM** only |
| `!resync` | Trigger GitHub resync (ops) |
| `!status` | Version / uptime / queue counts |
| `!ignore` / `!unignore` / `!ignored` | FR #75 |
| `!focus` / `!unfocus` | FR #68 / #113 |

Exit codes for dry-run: `0` ok, `2` skill incomplete.

## Diagnose

1. **Stale MRB after merge:** run dry-run; check counts; `!resync` or wait periodic resync.
2. **Accepted empty after ACK:** nick `{machine}-<pid>` in own shop; see `jeeves-worker-state`.
3. **FR vanished after FAIL + Closes #N:** K15 hold — dry-run shows `mrb_fail_hold`.
4. **Hand edit:** copy `queue.json`, dry-run inspect, edit a temp file, diff, then replace only with ops approval.

## Tests

```text
pytest -q tests/test_skill_queue_fr19.py tests/cast_iron/test_k4_supersede_merged.py tests/test_mrb_fail_closes_k15_fr16.py
```

Related: `jeeves-worker-state`, `jeeves-health`, `jeeves-announce-debug`.
