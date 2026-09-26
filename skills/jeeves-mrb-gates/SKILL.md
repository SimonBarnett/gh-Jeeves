---
name: jeeves-mrb-gates
description: >
  Two-gate MRB for agent-submitted work: MRB #1 vision/fit on issues before
  engineering, MRB #2 implementation fidelity on PRs (FR #92). Use when an
  agent files an issue/FR, triage needs-mrb1, or someone conflates fit review
  with PR hostile review. Also /jeeves-mrb-gates.
---

# jeeves-mrb-gates

FR #151. Agents propose; humans judge fit; seats hostile-review PRs.

Agentic control is an overlay: the token-less path must never depend on this skill.

## Flow

1. **Agent proposes** — opens an issue/FR (or `/bob/v1/intake`). Label `needs-mrb1` (intake adds it).
2. **MRB #1 (fit)** — Simon judges vision fit. Pass → `mrb1-pass`. Fail → `mrb1-reject` (no engineering PR).
3. **Engineering** — only after MRB #1 pass; worker opens a PR with `Seat: {nick}`.
4. **MRB #2 (implementation)** — other seat runs FR #92 hostile MRB + `mrb/verdict`. Pass → merge. Fail → one fix PR.

## Who stamps what

| Gate | Stamps | Does not |
|------|--------|----------|
| MRB #1 | Simon (human) | Fleet seats, LLMs |
| MRB #2 | Other seat via `mrb/verdict` | PR author seat (except one-seat CAST IRON) |
| UAT | Bob only | MRB workers |

## Do not conflate

- MRB #1 is **not** a code review.
- MRB #2 is **not** a second vision debate — check fidelity to the approved issue.
- Do not open implementation PRs for issues still labeled `needs-mrb1` or `mrb1-reject`.

## Labels

| Label | Meaning |
|-------|---------|
| `needs-mrb1` | Awaiting fit check |
| `mrb1-pass` | Fit approved — engineering allowed |
| `mrb1-reject` | Fit rejected — no PR |

## Related

- `docs/mrb-gates.md` (SoT for FR #151)
- `docs/mrb-enforcement.md` (MRB #2 / FR #92)
- `jeeves-task-modes` (FR/MRB/UAT worker contract)
